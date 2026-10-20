"""Pré-voo isolado da navegação OP455 CWB.

Não gera relatório, não consulta OP156 e não importa o runtime da OC73.
"""

import argparse
import json
import os
import secrets
import sys

from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)

from operations.op455.report import OP455Report  # noqa: E402
from ssw.client import SSWClient  # noqa: E402
from ssw.utils import dummy  # noqa: E402


VAR_USUARIO_AUTORIZADO = "OC73_PRE_VOO_SSW_USUARIO_AUTORIZADO"
FLAGS_SEGURAS = {
    "OCORRENCIA_73_ENABLED": "false",
    "OCORRENCIA_73_DRY_RUN": "true",
    "OCORRENCIA_73_MAX_LANCAMENTOS": "0",
    "OC73_ENVIO_HABILITADO": "NAO",
}

MATRIZ_ENDPOINTS = (
    ("POST", "/bin/ssw0422", "login", "act=L"),
    ("POST", "/bin/menu01", "abrir_menu", "act vazio"),
    ("GET", "/bin/ssw0082", "selecionar_unidade", "quadro_menu01=CWB"),
    ("POST", "/bin/menu01", "selecionar_op455", "act=TRO; f2=CWB; f3=455"),
    ("POST", "/bin/ssw0230", "abrir_op455", "sequencia=455"),
)


class PreVooBloqueado(RuntimeError):
    """Uma chamada saiu da sequência consultiva fechada."""


def _chaves_exatas(payload: dict, esperadas: set[str]) -> bool:
    return set(payload) == esperadas


class ClientePreVooOP455CWB:
    """Adaptador de transporte com sequência e payloads fechados."""

    def __init__(self, client: SSWClient) -> None:
        self._client = client
        self._etapa = 0
        self.chamadas: list[dict[str, str]] = []

    @property
    def usuario(self) -> str:
        return str(self._client.usuario or "")

    def _proxima(self, metodo: str, path: str) -> str:
        if self._etapa >= len(MATRIZ_ENDPOINTS):
            raise PreVooBloqueado("Pré-voo já encerrado; chamada adicional bloqueada.")
        esperado_metodo, esperado_path, acao, _ = MATRIZ_ENDPOINTS[self._etapa]
        if (metodo, path) != (esperado_metodo, esperado_path):
            raise PreVooBloqueado("Endpoint ou ordem fora da allowlist do pré-voo.")
        # Avança antes da rede: uma falha nunca permite repetir a mesma etapa.
        self._etapa += 1
        self.chamadas.append({"metodo": metodo, "endpoint": path, "acao": acao})
        return acao

    @staticmethod
    def _bloquear_acao_operacional(path: str, payload: dict) -> None:
        act = str(payload.get("act", "")).strip().upper()
        codigo = str(payload.get("f3", "")).strip()
        if path in {"/bin/ssw0122", "/bin/ssw1440", "/bin/ssw0424"}:
            raise PreVooBloqueado("Endpoint operacional proibido no pré-voo.")
        if act in {"E1", "II3"} or codigo == "73":
            raise PreVooBloqueado("Ação operacional proibida no pré-voo.")

    def post(self, path: str, data: dict | None = None, **_kwargs):
        payload = dict(data or {})
        self._bloquear_acao_operacional(path, payload)
        acao = self._proxima("POST", path)

        if acao == "login":
            valido = (
                _chaves_exatas(payload, {"act", "f1", "f2", "f3", "f4", "f6", "dummy"})
                and payload["act"] == "L"
                and payload["f6"] == "TRUE"
                and all(payload[campo] for campo in ("f1", "f2", "f3", "f4"))
                and bool(payload["dummy"])
            )
        elif acao == "abrir_menu":
            valido = payload == {"act": ""}
        elif acao == "selecionar_op455":
            valido = (
                _chaves_exatas(payload, {"act", "f2", "f3", "dummy"})
                and payload["act"] == "TRO"
                and payload["f2"] == "CWB"
                and payload["f3"] == "455"
                and bool(payload["dummy"])
            )
        elif acao == "abrir_op455":
            valido = (
                _chaves_exatas(payload, {"sequencia", "dummy"})
                and payload["sequencia"] == "455"
                and bool(payload["dummy"])
            )
        else:
            valido = False

        if not valido:
            raise PreVooBloqueado("Payload fora da allowlist do pré-voo.")
        return self._client.post(path, data=payload, retries=1)

    def get(self, path: str, params: dict | None = None, **_kwargs):
        payload = dict(params or {})
        self._bloquear_acao_operacional(path, payload)
        acao = self._proxima("GET", path)
        valido = (
            acao == "selecionar_unidade"
            and _chaves_exatas(payload, {"quadro_menu01", "dummy"})
            and payload["quadro_menu01"] == "CWB"
            and bool(payload["dummy"])
        )
        if not valido:
            raise PreVooBloqueado("Parâmetros fora da allowlist do pré-voo.")
        return self._client.get(path, params=payload, retries=1)

    def login(self) -> None:
        self.post(
            "/bin/ssw0422",
            data={
                "act": "L",
                "f1": self._client.dominio,
                "f2": self._client.cpf,
                "f3": self._client.usuario,
                "f4": self._client.senha,
                "f6": "TRUE",
                "dummy": dummy(),
            },
        )

    def open_menu(self) -> None:
        self.post("/bin/menu01", data={"act": ""})

    @property
    def concluido(self) -> bool:
        return self._etapa == len(MATRIZ_ENDPOINTS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida somente login, menu, unidade CWB e abertura da OP455. "
            "Não gera nem baixa relatório."
        ),
    )
    parser.add_argument(
        "--autorizar-pre-voo-cwb",
        action="store_true",
        required=True,
        help="Confirma autorização específica para esta única navegação consultiva.",
    )
    return parser


def _validar_flags() -> None:
    divergentes = [
        nome for nome, esperado in FLAGS_SEGURAS.items()
        if str(os.getenv(nome, "")).strip() != esperado
    ]
    if divergentes:
        raise PreVooBloqueado(
            "Flags de segurança ausentes ou divergentes: "
            + ", ".join(sorted(divergentes))
        )


def _validar_usuario(client: SSWClient) -> None:
    esperado = str(os.getenv(VAR_USUARIO_AUTORIZADO, "")).strip()
    observado = str(client.usuario or "").strip()
    if not esperado:
        raise PreVooBloqueado("Usuário SSW autorizado não foi informado.")
    if not observado or not secrets.compare_digest(observado, esperado):
        raise PreVooBloqueado("Usuário SSW diverge da autorização do pré-voo.")


def executar_pre_voo(client_factory=SSWClient) -> dict:
    _validar_flags()
    client_base = client_factory()
    _validar_usuario(client_base)
    client = ClientePreVooOP455CWB(client_base)

    client.login()
    client.open_menu()
    resultado = OP455Report(client).pre_voo_navegacao_ocorrencia_73_cwb()
    if not client.concluido:
        raise PreVooBloqueado("Sequência de navegação não foi concluída.")

    resultado["chamadas_http"] = len(client.chamadas)
    resultado["sequencia"] = list(client.chamadas)
    resultado["geracao_relatorio"] = False
    resultado["consulta_op156"] = False
    resultado["download"] = False
    return resultado


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    try:
        resultado = executar_pre_voo()
    except Exception as exc:
        saida = {
            "resultado": "bloqueado",
            "erro_tipo": type(exc).__name__,
            "geracao_relatorio": False,
            "consulta_op156": False,
            "download": False,
        }
        print(json.dumps(saida, ensure_ascii=False, sort_keys=True))
        return 3

    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
    return 0 if resultado["resultado"] == "contexto_confirmado" else 2


if __name__ == "__main__":
    sys.exit(main())
