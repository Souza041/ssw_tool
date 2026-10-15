"""Falhas offline da OC73 com cliente HTTP falso; nunca acessa o SSW."""

from datetime import date
from types import SimpleNamespace

import pytest

from modules.ocorrencia_73.gate import PortaoOC73
from operations.op101.ocorrencias import OP101Ocorrencias


DATA = date(2026, 10, 15)
REFERENCIA = "OC73-A1B2C3D4E5F60718293A"


class RepositorioFalso:
    def __init__(self):
        self.status = None
        self.envios_iniciados = 0
        self.incertos = 0
        self.confirmacoes = 0

    def reservar_ctrc(self, **kwargs):
        criada = self.status is None
        if criada:
            self.status = "reservado"
        return {
            "id": 501,
            "referencia": REFERENCIA,
            "status": self.status,
            "execucao_id": 101,
            "reserva_criada": criada,
        }

    def iniciar_envio(self, tentativa_id, execucao_id):
        if self.status != "reservado":
            return False
        self.status = "envio_iniciado"
        self.envios_iniciados += 1
        return True

    def marcar_incerto(self, *, tentativa_id, mensagem_erro):
        assert self.status == "envio_iniciado"
        self.status = "incerto"
        self.incertos += 1

    def confirmar_lancamento(self, *, tentativa_id, resposta_ssw):
        self.status = "confirmado"
        self.confirmacoes += 1


class ClienteHTTPFalso:
    def __init__(
        self,
        *,
        falha_envio=None,
        resposta_envio="REQUISICAO RECEBIDA",
        falha_historico=None,
        historico="<historico></historico>",
    ):
        self.falha_envio = falha_envio
        self.resposta_envio = resposta_envio
        self.falha_historico = falha_historico
        self.historico = historico
        self.posts_envio = 0
        self.consultas_historico = 0
        self.ultimo_payload_envio = None

    def get(self, path, **kwargs):
        return SimpleNamespace(text="73 - ENTREGA SERA REALIZADA AMANHA")

    def post(self, path, data):
        if path == "/bin/ssw0122":
            self.posts_envio += 1
            self.ultimo_payload_envio = data
            if self.falha_envio is not None:
                raise self.falha_envio
            return SimpleNamespace(text=self.resposta_envio)

        if path == "/bin/ssw0053" and data.get("act") == "O":
            self.consultas_historico += 1
            if self.falha_historico is not None:
                raise self.falha_historico
            return SimpleNamespace(text=self.historico)

        raise AssertionError(f"POST falso inesperado: {path}")


def processar(repository, client):
    op101 = OP101Ocorrencias(client)
    return PortaoOC73(repository).processar(
        execucao_id=101,
        serie="CWB",
        numero="100001",
        fluxo="CWB",
        data_inicial=DATA,
        data_final=DATA,
        enviar=lambda referencia: op101.lancar_ocorrencia_73(
            seq_ctrc="123456",
            familia="ROD",
            data_ocorrencia=DATA,
            observacao=f"BOT OC73 | REF:{referencia}",
            local="CWB",
        ),
        confirmar=lambda referencia: op101.confirmar_ocorrencia_73(
            serie="CWB",
            numero="100001",
            seq_ctrc="123456",
            local="CWB",
            familia="ROD",
            data_referencia=DATA,
            referencia=referencia,
        ),
    )


@pytest.mark.parametrize(
    "falha",
    [
        TimeoutError("timeout antes da resposta"),
        ConnectionError("conexão interrompida"),
    ],
)
def test_falha_no_post_fica_incerta_e_retry_nao_reenvia(falha):
    repository = RepositorioFalso()
    client = ClienteHTTPFalso(falha_envio=falha)

    primeiro = processar(repository, client)
    segundo = processar(repository, client)

    assert primeiro.status == "incerto"
    assert segundo.status == "bloqueado_reserva_existente"
    assert repository.status == "incerto"
    assert client.posts_envio == 1
    assert client.consultas_historico == 0


def test_resposta_http_positiva_ambigua_nao_confirma_e_nao_reenvia():
    repository = RepositorioFalso()
    client = ClienteHTTPFalso(
        resposta_envio="HTTP 200 - operação processada",
        historico="<historico><r><f5>63 - EM TRANSITO</f5></r></historico>",
    )

    primeiro = processar(repository, client)
    segundo = processar(repository, client)

    assert primeiro.status == "incerto"
    assert segundo.status == "bloqueado_reserva_existente"
    assert repository.confirmacoes == 0
    assert client.posts_envio == 1
    assert client.consultas_historico == 1
    assert client.ultimo_payload_envio["f6"] == (
        f"BOT OC73 | REF:{REFERENCIA}"
    )


def test_falha_na_consulta_pos_envio_fica_incerta_sem_segundo_post():
    repository = RepositorioFalso()
    client = ClienteHTTPFalso(
        falha_historico=TimeoutError("histórico indisponível"),
    )

    primeiro = processar(repository, client)
    segundo = processar(repository, client)

    assert primeiro.status == "incerto"
    assert segundo.status == "bloqueado_reserva_existente"
    assert repository.status == "incerto"
    assert client.posts_envio == 1
    assert client.consultas_historico == 1


def test_queda_apos_envio_iniciado_preserva_bloqueio_sem_retry():
    repository = RepositorioFalso()
    client = ClienteHTTPFalso(
        falha_envio=SystemExit("queda abrupta simulada"),
    )

    with pytest.raises(SystemExit, match="queda abrupta"):
        processar(repository, client)

    assert repository.status == "envio_iniciado"

    segundo = processar(repository, client)

    assert segundo.status == "bloqueado_reserva_existente"
    assert repository.status == "envio_iniciado"
    assert client.posts_envio == 1


def test_historico_sem_referencia_correspondente_nao_confirma():
    repository = RepositorioFalso()
    client = ClienteHTTPFalso(
        historico="""
            <historico><r>
              <f5>73 - ENTREGA SERA REALIZADA AMANHA</f5>
              <f6>BOT OC73 | REF:OC73-FFFFFFFFFFFFFFFFFFFF</f6>
            </r></historico>
        """,
    )

    resultado = processar(repository, client)

    assert resultado.status == "incerto"
    assert repository.status == "incerto"
    assert repository.confirmacoes == 0
    assert client.posts_envio == 1
    assert client.consultas_historico == 1


@pytest.mark.parametrize(
    "status_persistido",
    ["incerto", "envio_iniciado", "confirmado"],
)
def test_estado_persistido_nunca_autoriza_novo_post(status_persistido):
    repository = RepositorioFalso()
    repository.status = status_persistido
    client = ClienteHTTPFalso()

    resultado = processar(repository, client)

    assert resultado.status == "bloqueado_reserva_existente"
    assert repository.status == status_persistido
    assert client.posts_envio == 0
    assert client.consultas_historico == 0
