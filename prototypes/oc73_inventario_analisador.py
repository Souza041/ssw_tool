"""Analisador offline do inventário Windows da OC73.

O relatório bruto contém apenas observações locais. Evidências administrativas
são recebidas separadamente e nunca são confiadas sem um verificador externo de
procedência fornecido pelo chamador.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import ipaddress
import json
import ntpath
import re
from typing import Any, Callable, Mapping


class InventarioInvalido(ValueError):
    pass


class Classificacao(str, Enum):
    CANDIDATO = "CANDIDATO"
    NAO_CONFORME = "NÃO CONFORME"
    INDETERMINADO = "INDETERMINADO"


@dataclass(frozen=True)
class ResultadoInventario:
    classificacao: Classificacao
    motivos: tuple[str, ...]
    isolamento_comprovado: bool = False


@dataclass(frozen=True)
class ResultadoProcedencia:
    """Resultado emitido por integração externa; não é prova por si só."""

    comprovada: bool
    evidence_id: str
    inventory_sha256: str
    procedure_revision_sha256: str
    authority_reference: str


VerificadorProcedencia = Callable[[Mapping[str, Any]], ResultadoProcedencia]

TOPO = {
    "schema_version", "collected_at", "expires_at", "host_contract", "os",
    "services", "processes", "listeners", "volumes", "shares", "replication",
    "errors",
}
CONTRATO = {"host", "port", "schema", "user", "product", "version"}
SERVICO = {"status", "name", "state", "pid", "executable"}
PROCESSO = {
    "status", "pid", "name", "executable", "product_metadata",
    "version_metadata", "sha256", "signature_status", "datadir", "volume_id",
    "command_line_summary", "instance_marker", "stable_observation",
}
COMANDO = {"sensitive_arguments_redacted", "observed_flags"}
LISTENER = {"status", "local_address", "local_port", "pid"}
VOLUME = {"status", "id"}
SHARE = {"status", "path"}
REPLICACAO = {"status", "indicators"}
OS = {"status", "caption", "version"}

EVIDENCIA_EXTERNA_V1 = {
    "schema_version", "evidence_id", "instance_id", "responsible", "source",
    "observed_at", "valid_until", "procedure_revision_sha256",
    "document_reference", "process", "assertions",
}
EVIDENCIA_EXTERNA_V2 = EVIDENCIA_EXTERNA_V1 | {"inventory_sha256"}
PROCESSO_EXTERNO = {"pid", "instance_marker", "executable_path", "executable_sha256"}
AFIRMACOES_EXTERNAS = {
    "product", "volume_id", "volume_dedicated", "replication_absent",
}

STATUS_EVIDENCIA = {"OBSERVADO", "NÃO OBSERVADO", "INDETERMINADO", "NÃO APLICÁVEL"}
SEGREDO_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|connection\s*string|dsn)\s*[:=]"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARCADOR_RE = re.compile(
    r"^(?P<pid>\d+)\|(?P<data>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z)$"
)
IDENTIFICADOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
DOCUMENTO_RE = re.compile(r"^(?:https://[^\s]{1,480}|urn:oc73:[A-Za-z0-9._:-]{1,480})$")
FLAGS_PERMITIDAS_RE = re.compile(
    r"^(?:port:\d{1,5}|(?:server-id|relay-log|log-bin|defaults-file|defaults-extra-file):PRESENT)$"
)
ERROS_PERMITIDOS = {
    "OS_QUERY_FAILED", "SERVICE_QUERY_FAILED", "LISTENER_QUERY_FAILED",
    "PROCESS_QUERY_FAILED", "VOLUME_QUERY_FAILED", "SHARE_QUERY_FAILED",
    "HASH_FAILED", "SIGNATURE_FAILED", "PROCESS_CHANGED",
    "PROCESS_CHANGE_CONFIRMED", "PROCESS_OBSERVATION_INCOMPLETE",
}
PRODUTOS_METADATA = {
    "MYSQL_COMMUNITY", "MYSQL_UNKNOWN_EDITION", "MARIADB", "OTHER", "UNKNOWN",
}
FONTES_EXTERNAS = {
    "ADMINISTRATIVE_INSPECTION", "ASSET_INVENTORY", "CHANGE_RECORD",
}
ASSINATURAS = {"VALID", "NOT_SIGNED", "INVALID", "UNKNOWN"}
INSTANCIA_ESPERADA = "oc73-lab-127.0.0.1-3308"
NOMES_RESERVADOS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{numero}" for numero in range(1, 10)),
    *(f"LPT{numero}" for numero in range(1, 10)),
}


def _falhar() -> None:
    raise InventarioInvalido("Relatório de inventário inválido ou não sanitizado.")


def _objeto(valor: Any, campos: set[str]) -> dict[str, Any]:
    if not isinstance(valor, Mapping):
        _falhar()
    chaves = tuple(valor.keys())
    if not all(isinstance(chave, str) for chave in chaves) or set(chaves) != campos:
        _falhar()
    return dict(valor)


def _lista(valor: Any) -> list[Any]:
    if not isinstance(valor, list):
        _falhar()
    return valor


def _pares_sem_duplicidade(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    resultado: dict[str, Any] = {}
    for chave, valor in pares:
        if chave in resultado:
            _falhar()
        resultado[chave] = valor
    return resultado


def _texto(valor: Any, *, maximo: int, vazio: bool = False) -> str:
    if (
        not isinstance(valor, str)
        or len(valor) > maximo
        or (not vazio and not valor)
        or any(ord(caractere) < 32 or ord(caractere) == 127 for caractere in valor)
    ):
        _falhar()
    return valor


def _caminho_local(valor: Any, *, opcional: bool = False) -> str | None:
    if valor is None and opcional:
        return None
    caminho = _texto(valor, maximo=512)
    caminho_barras = caminho.replace("/", "\\")
    if caminho_barras.startswith(("\\\\", "\\?\\", "\\.\\", "\\??\\")):
        _falhar()
    if not re.fullmatch(r"[C-Zc-z]:\\[^:*?\"<>|]*", caminho_barras):
        _falhar()
    componentes = caminho_barras[3:].split("\\")
    if any(
        not componente
        or componente in {".", ".."}
        or componente.endswith((" ", "."))
        or componente.split(".", 1)[0].upper() in NOMES_RESERVADOS
        for componente in componentes
    ):
        _falhar()
    normalizado = ntpath.normpath(caminho_barras)
    if not re.fullmatch(r"[C-Zc-z]:\\.+", normalizado):
        _falhar()
    return normalizado


def _lista_limitada(valor: Any, limite: int) -> list[Any]:
    itens = _lista(valor)
    if len(itens) > limite:
        _falhar()
    return itens


def _status(objeto: Mapping[str, Any]) -> str:
    status = objeto.get("status")
    if not isinstance(status, str) or status not in STATUS_EVIDENCIA:
        _falhar()
    return status


def _instante(valor: Any) -> datetime:
    if not isinstance(valor, str) or len(valor) > 64:
        _falhar()
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        _falhar()
    if instante.tzinfo is None or instante.utcoffset() is None:
        _falhar()
    return instante.astimezone(timezone.utc)


def _validar_marcador(valor: Any, pid_esperado: int) -> str:
    marcador = _texto(valor, maximo=128)
    correspondencia = MARCADOR_RE.fullmatch(marcador)
    if correspondencia is None or int(correspondencia.group("pid")) != pid_esperado:
        _falhar()
    try:
        datetime.strptime(
            correspondencia.group("data")[:19], "%Y-%m-%dT%H:%M:%S"
        )
    except (ValueError, OverflowError):
        _falhar()
    return marcador


def _carregar_objeto(valor: str | Mapping[str, Any], campos: set[str]) -> dict[str, Any]:
    if isinstance(valor, str):
        try:
            tamanho = len(valor.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            _falhar()
        if tamanho > 262_144:
            _falhar()
        try:
            valor = json.loads(valor, object_pairs_hook=_pares_sem_duplicidade)
        except (json.JSONDecodeError, UnicodeError, OverflowError):
            _falhar()
    objeto = _objeto(valor, campos)
    try:
        serializado = json.dumps(objeto, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError, UnicodeError):
        _falhar()
    try:
        tamanho_serializado = len(serializado.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        _falhar()
    if tamanho_serializado > 262_144 or SEGREDO_RE.search(serializado):
        _falhar()
    return objeto


def _decodificar_json_bruto(conteudo: bytes) -> dict[str, Any]:
    if len(conteudo) > 262_144:
        _falhar()
    try:
        texto = conteudo.decode("utf-8", errors="strict")
        carregado = json.loads(texto, object_pairs_hook=_pares_sem_duplicidade)
    except (UnicodeDecodeError, json.JSONDecodeError, OverflowError):
        _falhar()
    return _objeto(carregado, TOPO)


def _carregar_relatorio(
    relatorio: bytes | str | Mapping[str, Any],
    bytes_originais: bytes | None,
) -> tuple[dict[str, Any], str | None]:
    if isinstance(relatorio, bytes):
        if bytes_originais is not None:
            _falhar()
        dados = _decodificar_json_bruto(relatorio)
        conteudo = relatorio
    elif isinstance(relatorio, str):
        if bytes_originais is not None:
            _falhar()
        try:
            conteudo = relatorio.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _falhar()
        dados = _decodificar_json_bruto(conteudo)
    else:
        dados = _carregar_objeto(relatorio, TOPO)
        if bytes_originais is None:
            conteudo = None
        else:
            if not isinstance(bytes_originais, bytes):
                _falhar()
            dados_dos_bytes = _decodificar_json_bruto(bytes_originais)
            if dados_dos_bytes != dados:
                _falhar()
            conteudo = bytes_originais

    try:
        serializado = json.dumps(dados, ensure_ascii=False)
        tamanho_serializado = len(serializado.encode("utf-8", errors="strict"))
    except (TypeError, ValueError, OverflowError, UnicodeError):
        _falhar()
    if tamanho_serializado > 262_144 or SEGREDO_RE.search(serializado):
        _falhar()
    digest = hashlib.sha256(conteudo).hexdigest() if conteudo is not None else None
    return dados, digest


def _validar_evidencia_externa(
    valor: str | Mapping[str, Any], *, agora: datetime
) -> tuple[dict[str, Any], list[str], bool]:
    if isinstance(valor, str):
        try:
            preliminar = json.loads(valor, object_pairs_hook=_pares_sem_duplicidade)
        except (json.JSONDecodeError, UnicodeError, OverflowError):
            _falhar()
    else:
        preliminar = valor
    if not isinstance(preliminar, Mapping):
        _falhar()
    versao = preliminar.get("schema_version")
    if versao == "oc73-administrative-evidence-v1":
        campos = EVIDENCIA_EXTERNA_V1
        vinculada_ao_inventario = False
    elif versao == "oc73-administrative-evidence-v2":
        campos = EVIDENCIA_EXTERNA_V2
        vinculada_ao_inventario = True
    else:
        _falhar()
    evidencia = _carregar_objeto(valor, campos)
    if vinculada_ao_inventario:
        digest = _texto(evidencia["inventory_sha256"], maximo=64)
        if not SHA256_RE.fullmatch(digest):
            _falhar()
    for campo in ("evidence_id", "instance_id"):
        texto = _texto(evidencia[campo], maximo=128)
        if not IDENTIFICADOR_RE.fullmatch(texto):
            _falhar()
    responsavel = _texto(evidencia["responsible"], maximo=160)
    if not responsavel.strip():
        _falhar()
    if not isinstance(evidencia["source"], str) or evidencia["source"] not in FONTES_EXTERNAS:
        _falhar()
    observado = _instante(evidencia["observed_at"])
    validade = _instante(evidencia["valid_until"])
    revisao = _texto(evidencia["procedure_revision_sha256"], maximo=64)
    if not SHA256_RE.fullmatch(revisao):
        _falhar()
    referencia = _texto(evidencia["document_reference"], maximo=512)
    if not DOCUMENTO_RE.fullmatch(referencia):
        _falhar()

    processo = _objeto(evidencia["process"], PROCESSO_EXTERNO)
    if type(processo["pid"]) is not int or processo["pid"] <= 0:
        _falhar()
    _validar_marcador(processo["instance_marker"], processo["pid"])
    processo["executable_path"] = _caminho_local(processo["executable_path"])
    hash_executavel = _texto(processo["executable_sha256"], maximo=64)
    if not SHA256_RE.fullmatch(hash_executavel):
        _falhar()
    evidencia["process"] = processo

    afirmacoes = _objeto(evidencia["assertions"], AFIRMACOES_EXTERNAS)
    if afirmacoes["product"] != "MYSQL_COMMUNITY_5_7_32":
        _falhar()
    afirmacoes["volume_id"] = _texto(afirmacoes["volume_id"], maximo=256).strip().casefold()
    if type(afirmacoes["volume_dedicated"]) is not bool:
        _falhar()
    if type(afirmacoes["replication_absent"]) is not bool:
        _falhar()
    evidencia["assertions"] = afirmacoes

    problemas: list[str] = []
    if validade <= observado or agora >= validade:
        problemas.append("Evidência administrativa externa expirada.")
    if observado > agora:
        problemas.append("Evidência administrativa externa possui data futura.")
    return evidencia, problemas, vinculada_ao_inventario


def analisar_relatorio(
    relatorio: bytes | str | Mapping[str, Any],
    *,
    agora: datetime | None = None,
    evidencia_externa: str | Mapping[str, Any] | None = None,
    verificador_procedencia: VerificadorProcedencia | None = None,
    inventario_bytes_originais: bytes | None = None,
    revisao_procedimento_aprovada: str | None = None,
) -> ResultadoInventario:
    dados, inventario_sha256 = _carregar_relatorio(
        relatorio, inventario_bytes_originais
    )
    motivos_nao_conforme: list[str] = []
    motivos_indeterminados: list[str] = []

    if dados["schema_version"] != "oc73-inventario-windows-v2":
        _falhar()
    coletado = _instante(dados["collected_at"])
    expira = _instante(dados["expires_at"])
    agora = agora or datetime.now(timezone.utc)
    if agora.tzinfo is None or agora.utcoffset() is None:
        _falhar()
    agora = agora.astimezone(timezone.utc)
    if expira <= coletado or agora >= expira:
        motivos_nao_conforme.append("Inventário bruto expirado.")

    contrato = _objeto(dados["host_contract"], CONTRATO)
    if contrato != {
        "host": "127.0.0.1", "port": 3308,
        "schema": "oc73_autorizacao_transacional_test", "user": "oc73_tester",
        "product": "MySQL Community", "version": "5.7.32",
    }:
        motivos_nao_conforme.append("Contrato literal do laboratório divergente.")

    sistema = _objeto(dados["os"], OS)
    if _status(sistema) == "OBSERVADO":
        _texto(sistema["caption"], maximo=128)
        _texto(sistema["version"], maximo=64)
    else:
        motivos_indeterminados.append("Versão do Windows não observada.")

    servicos = [_objeto(i, SERVICO) for i in _lista_limitada(dados["services"], 64)]
    processos = [_objeto(i, PROCESSO) for i in _lista_limitada(dados["processes"], 8)]
    listeners = [_objeto(i, LISTENER) for i in _lista_limitada(dados["listeners"], 16)]
    volumes = [_objeto(i, VOLUME) for i in _lista_limitada(dados["volumes"], 16)]
    shares = [_objeto(i, SHARE) for i in _lista_limitada(dados["shares"], 64)]
    replicacao = _objeto(dados["replication"], REPLICACAO)
    erros = _lista_limitada(dados["errors"], 32)
    if any(
        not isinstance(item, str) or item not in ERROS_PERMITIDOS
        for item in erros
    ):
        _falhar()
    if erros:
        motivos_indeterminados.append("Uma ou mais fontes locais falharam.")

    for servico in servicos:
        _status(servico)
        _texto(servico["name"], maximo=128)
        if (
            not isinstance(servico["state"], str)
            or servico["state"] not in {"RUNNING", "STOPPED", "OTHER"}
        ):
            _falhar()
        pid_servico = servico["pid"]
        if pid_servico is not None and (type(pid_servico) is not int or pid_servico <= 0):
            _falhar()
        if servico["state"] == "STOPPED" and pid_servico is not None:
            _falhar()
        servico["executable"] = _caminho_local(servico["executable"], opcional=True)
        if servico["executable"] and "xampp" in servico["executable"].casefold():
            motivos_nao_conforme.append("Serviço associado ao XAMPP detectado.")

    for processo in processos:
        _status(processo)
        if type(processo["pid"]) is not int or processo["pid"] <= 0:
            _falhar()
        _texto(processo["name"], maximo=128)
        processo["executable"] = _caminho_local(processo["executable"], opcional=True)
        processo["datadir"] = _caminho_local(processo["datadir"], opcional=True)
        if (
            not isinstance(processo["product_metadata"], str)
            or processo["product_metadata"] not in PRODUTOS_METADATA
        ):
            _falhar()
        if processo["version_metadata"] is not None:
            _texto(processo["version_metadata"], maximo=64)
        if (
            not isinstance(processo["signature_status"], str)
            or processo["signature_status"] not in ASSINATURAS
        ):
            _falhar()
        if processo["instance_marker"] is not None:
            _validar_marcador(processo["instance_marker"], processo["pid"])
        if type(processo["stable_observation"]) is not bool:
            _falhar()
        if processo["volume_id"] is not None:
            processo["volume_id"] = _texto(processo["volume_id"], maximo=256).strip().casefold()
        if processo["sha256"] is not None and (
            not isinstance(processo["sha256"], str) or not SHA256_RE.fullmatch(processo["sha256"])
        ):
            _falhar()
        resumo = _objeto(processo["command_line_summary"], COMANDO)
        if resumo["sensitive_arguments_redacted"] is not True:
            _falhar()
        flags = _lista_limitada(resumo["observed_flags"], 16)
        if not all(isinstance(i, str) and FLAGS_PERMITIDAS_RE.fullmatch(i) for i in flags):
            _falhar()
        processo["command_line_summary"] = resumo

    listeners_unicos: dict[tuple[str, int, int, str], Mapping[str, Any]] = {}
    for listener in listeners:
        _status(listener)
        if (
            type(listener["pid"]) is not int or listener["pid"] <= 0
            or type(listener["local_port"]) is not int
            or not 1 <= listener["local_port"] <= 65535
        ):
            _falhar()
        try:
            endereco = str(ipaddress.ip_address(_texto(listener["local_address"], maximo=64)))
        except ValueError:
            _falhar()
        listener["local_address"] = endereco
        chave = (endereco, listener["local_port"], listener["pid"], listener["status"])
        listeners_unicos[chave] = listener
    listeners = list(listeners_unicos.values())

    volumes_unicos: dict[str, Mapping[str, Any]] = {}
    for volume in volumes:
        _status(volume)
        if volume["id"] is None:
            continue
        volume_id = _texto(volume["id"], maximo=256).strip().casefold()
        if not volume_id:
            _falhar()
        volume["id"] = volume_id
        anterior = volumes_unicos.get(volume_id)
        if anterior is not None and anterior["status"] != volume["status"]:
            motivos_nao_conforme.append("Registros de volume contraditórios.")
        else:
            volumes_unicos[volume_id] = volume
    volumes = list(volumes_unicos.values())

    for share in shares:
        _status(share)
        share["path"] = _caminho_local(share["path"], opcional=True)

    _status(replicacao)
    indicadores = _lista_limitada(replicacao["indicators"], 8)
    if not all(
        isinstance(item, str) and item in {"server-id", "relay-log", "log-bin"}
        for item in indicadores
    ):
        _falhar()

    portas = [i for i in listeners if i["status"] == "OBSERVADO" and i["local_port"] == 3308]
    if not portas:
        motivos_indeterminados.append("Porta 3308 não observada.")
        return _resultado(motivos_nao_conforme, motivos_indeterminados)
    pids_porta = {i["pid"] for i in portas}
    if len(pids_porta) != 1 or any(i["local_address"] != "127.0.0.1" for i in portas):
        motivos_nao_conforme.append("Listener da porta 3308 inconsistente.")

    pid = next(iter(pids_porta)) if len(pids_porta) == 1 else None
    candidatos = [i for i in processos if i["pid"] == pid]
    if pid is not None and not candidatos and "PROCESS_QUERY_FAILED" in erros:
        motivos_indeterminados.append("Consulta do processo responsável falhou.")
        return _resultado(motivos_nao_conforme, motivos_indeterminados)
    if pid is None or len(candidatos) != 1:
        motivos_nao_conforme.append("PID da porta não corresponde a um único processo.")
        return _resultado(motivos_nao_conforme, motivos_indeterminados)
    processo = candidatos[0]
    if processo["status"] != "OBSERVADO":
        motivos_indeterminados.append("Processo responsável não foi observado integralmente.")
    if str(processo["name"]).lower() not in {"mysqld", "mysqld.exe"}:
        motivos_nao_conforme.append("Porta 3308 pertence a processo desconhecido.")

    executavel = processo["executable"]
    if executavel is None:
        motivos_indeterminados.append("Executável não identificado.")
    if "xampp" in str(executavel).lower() or "xampp" in str(processo["datadir"]).lower():
        motivos_nao_conforme.append("Caminho associado ao XAMPP detectado.")
    if processo["product_metadata"] == "MARIADB":
        motivos_nao_conforme.append("MariaDB não atende ao contrato MySQL Community.")
    elif processo["product_metadata"] == "OTHER":
        motivos_nao_conforme.append("Metadado local do produto é divergente.")
    elif (
        processo["product_metadata"] != "MYSQL_COMMUNITY"
        or processo["version_metadata"] != "5.7.32"
    ):
        motivos_indeterminados.append("Produto ou versão não confirmados pelos metadados locais.")

    if not isinstance(processo["sha256"], str) or not SHA256_RE.fullmatch(processo["sha256"]):
        motivos_indeterminados.append("Hash SHA-256 do executável ausente ou inválido.")
    if processo["signature_status"] != "VALID":
        motivos_indeterminados.append("Assinatura digital válida não observada.")
    if processo["datadir"] is None:
        motivos_indeterminados.append("Datadir não identificado.")
    if "PROCESS_CHANGE_CONFIRMED" in erros:
        motivos_nao_conforme.append("Mudança do processo confirmada entre observações.")
    elif (
        not processo["stable_observation"]
        and (
            "PROCESS_OBSERVATION_INCOMPLETE" in erros
            or "PROCESS_CHANGED" in erros
        )
    ):
        motivos_indeterminados.append("Identidade do processo não pôde ser confirmada entre observações.")
    elif not processo["stable_observation"] or not processo["instance_marker"]:
        motivos_nao_conforme.append("Processo mudou ou não foi correlacionado entre observações.")

    servicos_pid = [i for i in servicos if i["pid"] == pid]
    if len(servicos_pid) > 1:
        motivos_nao_conforme.append("Mais de um serviço reivindica o mesmo PID.")
    elif not servicos_pid:
        motivos_indeterminados.append("Serviço não correlacionado unicamente ao PID.")
    elif servicos_pid[0]["status"] != "OBSERVADO":
        motivos_indeterminados.append("Serviço correlacionado possui evidência incompleta.")
    elif servicos_pid[0]["executable"] != executavel:
        motivos_nao_conforme.append("Executável do serviço diverge do processo.")

    volume_id = processo["volume_id"]
    volumes_id = [i for i in volumes if i["id"] == volume_id]
    if not volume_id or len(volumes_id) != 1:
        motivos_indeterminados.append("Volume observado do datadir não foi associado unicamente.")

    datadir = str(processo["datadir"] or "").casefold().rstrip("\\/")
    for share in shares:
        caminho = str(share["path"] or "").casefold().rstrip("\\/")
        if caminho and datadir and (datadir == caminho or datadir.startswith(caminho + "\\")):
            motivos_nao_conforme.append("Datadir contido em compartilhamento local.")

    if replicacao["status"] == "OBSERVADO" and indicadores:
        motivos_nao_conforme.append("Indício local de replicação detectado.")

    if evidencia_externa is None:
        motivos_indeterminados.extend((
            "Identidade administrativa do produto não comprovada.",
            "Dedicação administrativa do volume não comprovada.",
            "Ausência administrativa de replicação não comprovada.",
        ))
    else:
        evidencia, problemas, evidencia_vinculada = _validar_evidencia_externa(
            evidencia_externa, agora=agora
        )
        motivos_nao_conforme.extend(problemas)

        precondicoes_procedencia = True
        if not evidencia_vinculada:
            motivos_indeterminados.append(
                "Evidência administrativa v1 não está vinculada ao inventário bruto."
            )
            precondicoes_procedencia = False
        elif inventario_sha256 is None:
            motivos_indeterminados.append(
                "Bytes originais do inventário não foram fornecidos para vinculação criptográfica."
            )
            precondicoes_procedencia = False
        elif evidencia["inventory_sha256"] != inventario_sha256:
            motivos_nao_conforme.append("Hash do inventário diverge da evidência externa.")
            precondicoes_procedencia = False

        if revisao_procedimento_aprovada is None:
            motivos_indeterminados.append("Revisão aprovada do procedimento não foi fornecida.")
            precondicoes_procedencia = False
        elif (
            not isinstance(revisao_procedimento_aprovada, str)
            or not SHA256_RE.fullmatch(revisao_procedimento_aprovada)
        ):
            _falhar()
        elif evidencia["procedure_revision_sha256"] != revisao_procedimento_aprovada:
            motivos_nao_conforme.append("Revisão do procedimento diverge da revisão aprovada.")
            precondicoes_procedencia = False

        procedencia_verificada = False
        if precondicoes_procedencia and verificador_procedencia is not None:
            try:
                verificacao = verificador_procedencia(evidencia)
            except Exception:
                verificacao = None
            if isinstance(verificacao, ResultadoProcedencia):
                procedencia_verificada = (
                    verificacao.comprovada is True
                    and isinstance(verificacao.authority_reference, str)
                    and bool(verificacao.authority_reference.strip())
                    and verificacao.evidence_id == evidencia["evidence_id"]
                    and verificacao.inventory_sha256 == inventario_sha256
                    and verificacao.procedure_revision_sha256
                    == revisao_procedimento_aprovada
                )
        if not procedencia_verificada:
            motivos_indeterminados.append(
                "Procedência da evidência administrativa externa não verificada por autoridade independente."
            )

        processo_externo = evidencia["process"]
        afirmacoes = evidencia["assertions"]
        if evidencia["instance_id"] != INSTANCIA_ESPERADA:
            motivos_nao_conforme.append("Evidência externa vinculada a outra instância.")
        if (
            processo_externo["pid"] != processo["pid"]
            or processo_externo["instance_marker"] != processo["instance_marker"]
            or processo_externo["executable_path"] != processo["executable"]
            or processo_externo["executable_sha256"] != processo["sha256"]
        ):
            motivos_nao_conforme.append("Evidência externa vinculada a outro processo ou executável.")
        if afirmacoes["volume_id"] != volume_id:
            motivos_nao_conforme.append("Evidência externa vinculada a outro volume.")
        if not afirmacoes["volume_dedicated"]:
            motivos_nao_conforme.append("Evidência administrativa informa volume não dedicado.")
        if not afirmacoes["replication_absent"]:
            motivos_nao_conforme.append("Ausência administrativa de replicação não confirmada.")

    return _resultado(motivos_nao_conforme, motivos_indeterminados)


def _resultado(nao_conforme: list[str], indeterminados: list[str]) -> ResultadoInventario:
    if nao_conforme:
        return ResultadoInventario(Classificacao.NAO_CONFORME, tuple(dict.fromkeys(nao_conforme)))
    if indeterminados:
        return ResultadoInventario(Classificacao.INDETERMINADO, tuple(dict.fromkeys(indeterminados)))
    return ResultadoInventario(
        Classificacao.CANDIDATO,
        ("Observações e evidência externa verificada são coerentes; isolamento ainda exige decisão humana.",),
        isolamento_comprovado=False,
    )
