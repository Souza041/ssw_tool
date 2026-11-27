"""Aplicativo mínimo de bandeja do Nexus Local Agent empacotável pelo PyInstaller."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
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


OFFICIAL_SERVER = "https://nexus.rodobrastransp.com.br"


def _configure_logging() -> Path:
    path = default_config_path().with_name("agent.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for name in (__name__, "modules.local_agent.remote_auth"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
    return path


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


def _proxy_session(server_url: str = OFFICIAL_SERVER) -> requests.Session:
    """Usa proxy do usuário Windows ou PROXY_* privado, sem registrar credenciais."""
    session = requests.Session()
    session.proxies.update({
        key: value for key, value in urllib.request.getproxies().items()
        if key in {"http", "https"}
    })
    # Optional per-user, Nexus-only override approved by corporate IT.
    proxy_file = default_config_path().with_name("nexus-proxy.txt")
    if proxy_file.is_file():
        explicit = proxy_file.read_text(encoding="utf-8-sig").strip()
        parsed = urlsplit(explicit)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or any(c.isspace() for c in explicit)):
            raise ValueError("Proxy Nexus inválido.")
        session.trust_env = False
        session.proxies = {"http": explicit, "https": explicit}
        return session
    from modules.local_agent.windows_proxy import resolve_pac
    automatic = resolve_pac(server_url)
    if automatic is not None:
        session.trust_env = False
        session.proxies = {"http": automatic, "https": automatic} if automatic else {}
        logging.getLogger(__name__).info("Nexus proxy mode: %s", "PAC" if automatic else "PAC_DIRECT")
        return session
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
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Informe a URL HTTPS oficial do Nexus.")
    response = _proxy_session(server_url).post(
        server_url.strip().rstrip("/") + "/api/local-agent/v1/pair",
        json={"code": code.strip().upper(), "machine_name": platform.node() or "windows"},
        timeout=30,
    )
    if response.status_code != 201:
        raise RuntimeError(f"Pareamento recusado pelo servidor (HTTP {response.status_code}).")
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
    import secrets
    import time

    root = tk.Tk()
    root.withdraw()
    try:
        usuario = simpledialog.askstring("Nexus Local Agent", "Usuário SSW (ROD):", parent=root)
        if not usuario:
            raise RuntimeError("Pareamento cancelado.")
        token = secrets.token_urlsafe(48)
        session = _proxy_session()
        url = OFFICIAL_SERVER + "/api/local-agent/v1/enrollment"
        response = session.post(url, json={"usuario":usuario.strip(),"machine_name":(platform.node() or "windows")[:64],"token":token}, timeout=30)
        response.raise_for_status()
        ref = response.json()["id"]
        # Persist the private proof before polling so a lost response can be recovered.
        pending = config_path.with_name("enrollment.json")
        save_config(pending, {"id":ref,"proof_dpapi":protect_token(token)})
        messagebox.showinfo("Nexus Local Agent", "Informe esta referência à TI para aprovação:\n" + ref, parent=root)
        return _finish_enrollment(config_path, pending, session, root)
    except Exception:
        messagebox.showerror("Nexus Local Agent", "Falha na autorização. Confira a rede e consulte a TI.", parent=root)
        raise
    finally:
        root.destroy()


def _finish_enrollment(config_path, pending, session, root=None):
    import json
    import time
    data = json.loads(pending.read_text(encoding="utf-8"))
    token = unprotect_token(data["proof_dpapi"])
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        response = session.post(OFFICIAL_SERVER + "/api/local-agent/v1/enrollment/" + data["id"] + "/poll", json={"token":token}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result["state"] == "ISSUED":
            config = {"server_url":OFFICIAL_SERVER,"agent_id":result["agent_id"],"token_dpapi":protect_token(token),"tasks":result["tasks"]}
            save_config(config_path, config)
            pending.unlink()
            return config
        if result["state"] not in {"PENDING","APPROVED"}:
            pending.unlink()
            raise RuntimeError("Solicitação encerrada pela TI ou expirada.")
        time.sleep(5)
    raise RuntimeError("Prazo de autorização encerrado.")


def _apply_config(config: dict) -> None:
    from modules.local_agent.config import individual_task_grants

    tasks = individual_task_grants(frozenset(config["tasks"]))
    os.environ.update({
        "NEXUS_LOCAL_AGENT_SERVER_URL": str(config["server_url"]),
        "NEXUS_LOCAL_AGENT_ID": str(config["agent_id"]),
        "NEXUS_LOCAL_AGENT_TOKEN": unprotect_token(str(config["token_dpapi"])),
        "NEXUS_LOCAL_AGENT_POLL_SECONDS": "5",
        "NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS": "15",
        "NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP": "false",
        "NEXUS_SSW_REMOTE_AUTH_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_SSW_TASKS": ",".join(sorted(tasks - {"system.ping"})),
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
        self.client = AgentClient(settings, session=_proxy_session(settings.server_url))
        self.executor = LocalAgentExecutor(self.client, registry)
        self.remote_auth = WindowsRemoteAuthExecutor(self.client)
        self.poll_seconds = settings.poll_seconds
        self.status = "Conectando"

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                worked = self.remote_auth.run_once()
                if not worked and not self.stop_event.is_set():
                    worked = self.executor.run_once()
                self.status = "Conectado"
            except (AgentProtocolError, RemoteAuthError, requests.RequestException):
                logging.getLogger(__name__).warning("Agent communication failed")
                worked = False
                self.status = "Desconectado"
            except Exception:
                logging.getLogger(__name__).warning("Agent runtime failed")
                worked = False
                self.status = "Atenção necessária"
            if not worked:
                self.stop_event.wait(self.poll_seconds)


def _run_tray(runtime: AgentRuntime) -> bool:
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#07111f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="#31d0aa")
    draw.text((23, 18), "N", fill="#07111f")

    def title(_item):
        return runtime.status

    repair = False

    def stop(icon, _item):
        runtime.stop_event.set()
        icon.stop()

    def repair_pairing(icon, _item):
        nonlocal repair
        # The worker must finish its current operation before enrollment starts.
        repair = True
        stop(icon, _item)

    def diagnostic(icon, _item):
        os.startfile(str(default_config_path().with_name("agent.log")))

    icon = pystray.Icon(
        "NexusLocalAgent", image, "Nexus Local Agent",
        menu=pystray.Menu(pystray.MenuItem(title, None, enabled=False), pystray.MenuItem("Abrir diagnóstico", diagnostic), pystray.MenuItem("Refazer pareamento (após concluir tarefas)", repair_pairing), pystray.MenuItem("Sair", stop)),
    )
    worker = threading.Thread(target=runtime.run, name="nexus-agent", daemon=False)
    worker.start()
    try:
        icon.run()
    finally:
        runtime.stop_event.set()
        worker.join()
    return repair


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url")
    parser.add_argument("--pair-code")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    mutex = _single_instance()
    _configure_logging()
    _prepare_playwright()
    if args.server_url or args.pair_code:
        if not args.server_url or not args.pair_code:
            parser.error("--server-url e --pair-code devem ser usados juntos.")
        config = pair(args.server_url, args.pair_code, args.config)
    elif args.config.is_file():
        config = load_config(args.config)
    elif args.config.with_name("enrollment.json").is_file():
        config = _finish_enrollment(args.config, args.config.with_name("enrollment.json"), _proxy_session())
    else:
        config = _interactive_pair(args.config)
    _apply_config(config)
    runtime = AgentRuntime()
    if args.once:
        runtime.remote_auth.run_once() or runtime.executor.run_once()
        return 0
    if _run_tray(runtime):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo("Nexus Local Agent", "Tarefas encerradas. Solicite à TI a revogação da autorização anterior antes de parear novamente. A configuração atual será preservada até a nova aprovação.", parent=root)
        finally:
            root.destroy()
        pending = args.config.with_name("enrollment.json")
        if pending.is_file():
            _finish_enrollment(args.config, pending, _proxy_session())
        else:
            _interactive_pair(args.config)
        # Restart manually to avoid sharing browser sessions across identities.
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo("Nexus Local Agent", "Autorização salva. Abra o agente novamente.", parent=root)
        finally:
            root.destroy()
    _ = mutex
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
