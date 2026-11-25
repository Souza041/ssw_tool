"""Aplicativo mínimo de bandeja do Nexus Local Agent empacotável pelo PyInstaller."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
import threading
import urllib.request
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import requests

from modules.local_agent.config import load_executor_settings
from modules.local_agent.executor import (
    AgentClient, AgentProtocolError, HandlerRegistry, LocalAgentExecutor,
    register_builtin_handlers,
)
from modules.local_agent.remote_auth import RemoteAuthError, WindowsRemoteAuthExecutor
from modules.local_agent.ssw_tasks import register_ssw_handlers
from modules.local_agent.windows_credentials import (
    default_config_path, load_config, protect_token, save_config, unprotect_token,
)


def _resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / name


def _prepare_playwright() -> None:
    bundled = _resource_path("playwright-browsers")
    if bundled.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


def _single_instance():
    if os.name != "nt":
        return None
    handle = __import__("ctypes").windll.kernel32.CreateMutexW(
        None, False, "Local\\NexusLocalAgentInteractive"
    )
    if __import__("ctypes").windll.kernel32.GetLastError() == 183:
        raise RuntimeError("O Nexus Local Agent já está em execução nesta sessão Windows.")
    return handle


def _proxy_session() -> requests.Session:
    """Usa proxy do usuário Windows ou PROXY_* privado, sem registrar credenciais."""
    session = requests.Session()
    session.proxies.update({
        key: value for key, value in urllib.request.getproxies().items()
        if key in {"http", "https"}
    })
    server = os.getenv("PROXY_SERVER", "").strip().rstrip("/")
    username = os.getenv("PROXY_USER", "").strip()
    password = os.getenv("PROXY_PASS", "")
    if server:
        parsed = urlsplit(server)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Configuração de proxy inválida.")
        if bool(username) != bool(password):
            raise ValueError("Configuração de proxy incompleta.")
        netloc = parsed.netloc
        if username:
            netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{parsed.netloc}"
        proxy_url = urlunsplit((parsed.scheme, netloc, "", "", ""))
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return session


def pair(server_url: str, code: str, config_path: Path) -> dict:
    parsed = urlparse(server_url.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Informe a URL HTTPS oficial do Nexus.")
    response = _proxy_session().post(
        server_url.strip().rstrip("/") + "/api/local-agent/v1/pair",
        json={"code": code.strip().upper(), "machine_name": platform.node() or "windows"},
        timeout=30,
    )
    if response.status_code != 201:
        raise RuntimeError("Código inválido, expirado ou já utilizado.")
    payload = response.json()
    config = {
        "server_url": server_url.strip().rstrip("/"),
        "agent_id": str(payload["agent_id"]),
        "token_dpapi": protect_token(str(payload["token"])),
        "tasks": list(payload["tasks"]),
    }
    save_config(config_path, config)
    return config


def _interactive_pair(config_path: Path) -> dict:
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    server_url = simpledialog.askstring("Nexus Local Agent", "URL HTTPS do Nexus:", parent=root)
    code = simpledialog.askstring("Nexus Local Agent", "Código temporário de pareamento:", parent=root)
    if not server_url or not code:
        raise RuntimeError("Pareamento cancelado.")
    try:
        config = pair(server_url, code, config_path)
    except Exception as exc:
        messagebox.showerror("Nexus Local Agent", str(exc), parent=root)
        raise
    messagebox.showinfo("Nexus Local Agent", "Computador pareado com sucesso.", parent=root)
    root.destroy()
    return config


def _apply_config(config: dict) -> None:
    os.environ.update({
        "NEXUS_LOCAL_AGENT_SERVER_URL": str(config["server_url"]),
        "NEXUS_LOCAL_AGENT_ID": str(config["agent_id"]),
        "NEXUS_LOCAL_AGENT_TOKEN": unprotect_token(str(config["token_dpapi"])),
        "NEXUS_LOCAL_AGENT_POLL_SECONDS": "5",
        "NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS": "15",
        "NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP": "false",
        "NEXUS_SSW_REMOTE_AUTH_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_SSW_TASKS": ",".join(str(item) for item in config["tasks"] if str(item) != "system.ping"),
    })
    runtime = default_config_path().with_name("runtime.env")
    if runtime.is_file():
        from modules.local_agent.private_env import SSW_RUNTIME_PREFIXES, load_private_environment
        load_private_environment(runtime, allowed_prefixes=SSW_RUNTIME_PREFIXES)
    os.environ.setdefault("SSW_BASE_URL", "https://tms.ssw.inf.br")
    os.environ.setdefault("SSW_AUTH_MODE", "browser")
    os.environ.setdefault("SSW_HUMAN_LOGIN_TIMEOUT", "900")


class AgentRuntime:
    def __init__(self) -> None:
        settings = load_executor_settings()
        registry = HandlerRegistry()
        register_builtin_handlers(registry)
        register_ssw_handlers(registry)
        self.stop_event = threading.Event()
        self.client = AgentClient(settings, session=_proxy_session())
        self.executor = LocalAgentExecutor(self.client, registry)
        self.remote_auth = WindowsRemoteAuthExecutor(self.client)
        self.poll_seconds = settings.poll_seconds
        self.status = "Conectando"

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                worked = self.remote_auth.run_once()
                if not worked:
                    worked = self.executor.run_once()
                self.status = "Conectado"
            except (AgentProtocolError, RemoteAuthError, requests.RequestException):
                worked = False
                self.status = "Desconectado"
            except Exception:
                worked = False
                self.status = "Atenção necessária"
            if not worked:
                self.stop_event.wait(self.poll_seconds)


def _run_tray(runtime: AgentRuntime) -> None:
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#07111f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="#31d0aa")
    draw.text((23, 18), "N", fill="#07111f")

    def title(_item):
        return runtime.status

    def stop(icon, _item):
        runtime.stop_event.set()
        icon.stop()

    icon = pystray.Icon(
        "NexusLocalAgent", image, "Nexus Local Agent",
        menu=pystray.Menu(pystray.MenuItem(title, None, enabled=False), pystray.MenuItem("Sair", stop)),
    )
    threading.Thread(target=runtime.run, name="nexus-agent", daemon=True).start()
    icon.run()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url")
    parser.add_argument("--pair-code")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    mutex = _single_instance()
    _prepare_playwright()
    if args.server_url or args.pair_code:
        if not args.server_url or not args.pair_code:
            parser.error("--server-url e --pair-code devem ser usados juntos.")
        config = pair(args.server_url, args.pair_code, args.config)
    elif args.config.is_file():
        config = load_config(args.config)
    else:
        config = _interactive_pair(args.config)
    _apply_config(config)
    runtime = AgentRuntime()
    if args.once:
        runtime.remote_auth.run_once() or runtime.executor.run_once()
        return 0
    _run_tray(runtime)
    _ = mutex
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
