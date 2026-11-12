"""Probes passivos com allowlist e respostas técnicas sanitizadas."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

from modules.health_center.dependency_models import (
    DependencyDefinition, DependencyState, ProbeKind, ProbeResult,
)


Resolver = Callable[[str, int], Awaitable[list[tuple]]]


def allowed_hosts_from_env() -> frozenset[str]:
    return frozenset(
        item.strip().lower().rstrip(".")
        for item in os.getenv("HEALTH_DEPENDENCY_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    )


def _safe_host(host: str) -> str:
    value = host.strip().lower().rstrip(".")
    if not value or any(char in value for char in ("/", "\\", "@", "#", "?")):
        raise ValueError("Host inválido.")
    return value


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    )


async def _default_resolver(host: str, port: int) -> list[tuple]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def resolve_authorized(
    definition: DependencyDefinition, *, allowed_hosts: frozenset[str],
    resolver: Resolver = _default_resolver,
) -> list[str]:
    host = _safe_host(definition.host)
    if host not in allowed_hosts:
        raise PermissionError("Destino fora da allowlist.")
    records = await resolver(host, definition.port)
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise OSError("Resolução sem endereço.")
    if not definition.allow_private and any(not _public_address(item) for item in addresses):
        raise PermissionError("Endereço privado ou especial bloqueado.")
    return addresses


def _result(
    definition: DependencyDefinition, probe: ProbeKind, state: DependencyState,
    started: float, reason: str, detail: str, *, http_status: int | None = None,
    metadata: dict | None = None,
) -> ProbeResult:
    return ProbeResult(
        dependency_id=definition.identifier,
        probe=probe,
        state=state,
        checked_at=datetime.now(timezone.utc),
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        reason_code=reason,
        detail=detail,
        http_status=http_status,
        metadata=metadata or {},
    )


def _failure(definition: DependencyDefinition, probe: ProbeKind, started: float,
             error: BaseException) -> ProbeResult:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        reason = "timeout"
    elif isinstance(error, ssl.SSLCertVerificationError):
        reason = "tls_certificate_invalid"
    elif isinstance(error, socket.gaierror):
        reason = "dns_resolution_failed"
    elif isinstance(error, PermissionError):
        reason = "destination_blocked"
    elif isinstance(error, (ConnectionError, OSError)):
        reason = "connection_failed"
    else:
        reason = "probe_failed"
    return _result(
        definition, probe, DependencyState.DOWN, started, reason,
        "Verificação falhou; endereço, credenciais e exceção foram omitidos.",
    )


async def probe_dns(
    definition: DependencyDefinition, *, timeout: float,
    allowed_hosts: frozenset[str], resolver: Resolver = _default_resolver,
) -> ProbeResult:
    started = time.perf_counter()
    try:
        addresses = await asyncio.wait_for(
            resolve_authorized(definition, allowed_hosts=allowed_hosts, resolver=resolver),
            timeout=timeout,
        )
        return _result(
            definition, ProbeKind.DNS, DependencyState.HEALTHY, started,
            "dns_resolved", "DNS resolvido para endereço autorizado.",
            metadata={"address_count": len(addresses)},
        )
    except Exception as error:
        return _failure(definition, ProbeKind.DNS, started, error)


async def probe_tcp(
    definition: DependencyDefinition, *, timeout: float,
    allowed_hosts: frozenset[str], resolver: Resolver = _default_resolver,
) -> ProbeResult:
    started = time.perf_counter()
    writer = None
    try:
        addresses = await asyncio.wait_for(
            resolve_authorized(definition, allowed_hosts=allowed_hosts, resolver=resolver),
            timeout=timeout,
        )
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(addresses[0], definition.port), timeout=timeout,
        )
        return _result(
            definition, ProbeKind.TCP, DependencyState.HEALTHY, started,
            "tcp_connected", "Conexão TCP estabelecida e encerrada sem envio de dados.",
        )
    except Exception as error:
        return _failure(definition, ProbeKind.TCP, started, error)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def probe_tls(
    definition: DependencyDefinition, *, timeout: float,
    allowed_hosts: frozenset[str], resolver: Resolver = _default_resolver,
) -> ProbeResult:
    started = time.perf_counter()
    writer = None
    try:
        addresses = await asyncio.wait_for(
            resolve_authorized(definition, allowed_hosts=allowed_hosts, resolver=resolver),
            timeout=timeout,
        )
        context = ssl.create_default_context()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                addresses[0], definition.port, ssl=context,
                server_hostname=definition.host,
            ),
            timeout=timeout,
        )
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert() if ssl_object else {}
        expires = certificate.get("notAfter")
        days = None
        if expires:
            expiry = datetime.strptime(expires, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
            days = max(0, (expiry - datetime.now(timezone.utc)).days)
        state = DependencyState.DEGRADED if days is not None and days < 14 else DependencyState.HEALTHY
        return _result(
            definition, ProbeKind.TLS, state, started,
            "tls_expiring" if state is DependencyState.DEGRADED else "tls_valid",
            "Handshake TLS válido; conteúdo do certificado não é exposto.",
            metadata={"certificate_days_remaining": days},
        )
    except Exception as error:
        return _failure(definition, ProbeKind.TLS, started, error)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def _http_request(definition: DependencyDefinition, timeout: float, address: str):
    method = str(definition.metadata.get("http_method", "HEAD")).upper()
    if method not in {"HEAD", "GET"}:
        raise ValueError("Método HTTP não permitido.")
    # O socket usa o IP previamente validado, evitando uma segunda resolução DNS.
    raw_socket = socket.create_connection((address, definition.port), timeout=timeout)
    if definition.scheme == "https":
        context = ssl.create_default_context()
        network_socket = context.wrap_socket(raw_socket, server_hostname=definition.host)
    else:
        network_socket = raw_socket
    connection = http.client.HTTPConnection(
        definition.host, definition.port, timeout=timeout,
    )
    connection.sock = network_socket
    try:
        connection.request(method, definition.path, headers={
            "User-Agent": "Nexus-Health-Dependency-Monitor/1",
            "Accept": "text/html,application/json;q=0.8,*/*;q=0.1",
            "Connection": "close",
        })
        response = connection.getresponse()
        response.read(1024)
        return response.status, response.getheader("Location")
    finally:
        connection.close()


async def probe_http(
    definition: DependencyDefinition, *, timeout: float,
    allowed_hosts: frozenset[str], resolver: Resolver = _default_resolver,
) -> ProbeResult:
    started = time.perf_counter()
    try:
        addresses = await asyncio.wait_for(
            resolve_authorized(definition, allowed_hosts=allowed_hosts, resolver=resolver),
            timeout=timeout,
        )
        status, location = await asyncio.wait_for(
            asyncio.to_thread(_http_request, definition, timeout, addresses[0]),
            timeout=timeout + 0.5,
        )
        if 300 <= status < 400 and location:
            target = urlparse(urljoin(
                f"{definition.scheme}://{definition.host}:{definition.port}{definition.path}",
                location,
            ))
            if _safe_host(target.hostname or "") not in allowed_hosts:
                return _result(
                    definition, ProbeKind.HTTP, DependencyState.DOWN, started,
                    "redirect_blocked", "Redirecionamento externo bloqueado pela allowlist.",
                    http_status=status,
                )
            return _result(
                definition, ProbeKind.HTTP, DependencyState.DEGRADED, started,
                "redirect_not_followed", "Redirecionamento autorizado não foi seguido.",
                http_status=status,
            )
        state = DependencyState.DEGRADED if status >= 500 else DependencyState.HEALTHY
        return _result(
            definition, ProbeKind.HTTP, state, started,
            "http_server_error" if status >= 500 else "http_reachable_not_functional",
            "Resposta HTTP confirma alcance técnico, não disponibilidade funcional.",
            http_status=status,
            metadata={"functional_availability_proven": False},
        )
    except Exception as error:
        return _failure(definition, ProbeKind.HTTP, started, error)


async def probe_mysql(
    definition: DependencyDefinition, *, timeout: float,
    allowed_hosts: frozenset[str], resolver: Resolver = _default_resolver,
    connector=None,
) -> ProbeResult:
    started = time.perf_counter()
    try:
        addresses = await asyncio.wait_for(
            resolve_authorized(definition, allowed_hosts=allowed_hosts, resolver=resolver),
            timeout=timeout,
        )
        prefix = definition.mysql_env_prefix
        if not prefix:
            raise ValueError("Prefixo MySQL ausente.")
        if connector is None:
            import pymysql
            connector = pymysql.connect
        config = {
            "host": addresses[0],
            "port": definition.port,
            "user": os.getenv(f"{prefix}_USER", ""),
            "password": os.getenv(f"{prefix}_PASSWORD", ""),
            "database": os.getenv(f"{prefix}_NAME", ""),
            "connect_timeout": timeout,
            "read_timeout": timeout,
            "write_timeout": timeout,
            "autocommit": False,
        }
        if not config["user"] or not config["password"] or not config["database"]:
            raise ValueError("Configuração MySQL incompleta.")

        def read_only_check():
            connection = connector(**config)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
                connection.rollback()
                return row
            finally:
                connection.close()

        await asyncio.wait_for(asyncio.to_thread(read_only_check), timeout=timeout + 0.5)
        return _result(
            definition, ProbeKind.MYSQL, DependencyState.HEALTHY, started,
            "mysql_read_only_ok", "Consulta constante em transação somente leitura concluída.",
        )
    except Exception as error:
        return _failure(definition, ProbeKind.MYSQL, started, error)


PROBES = {
    ProbeKind.DNS: probe_dns,
    ProbeKind.TCP: probe_tcp,
    ProbeKind.TLS: probe_tls,
    ProbeKind.HTTP: probe_http,
    ProbeKind.MYSQL: probe_mysql,
}
