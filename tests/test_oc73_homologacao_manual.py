"""Testes offline do comando manual; nenhuma rede ou automação é iniciada."""

from types import SimpleNamespace

import pytest

from scripts import testar_ocorrencia_73 as comando


class ServiceFake:
    def __init__(self, resultado=None):
        self.resultado = resultado or {"status": "simulado"}
        self.chamadas = []

    def executar(self, **kwargs):
        self.chamadas.append(kwargs)
        return self.resultado


def preparar(monkeypatch, *, dry_run=True, gate=False, resultado=None):
    instancia = ServiceFake(resultado)
    monkeypatch.setattr(comando, "load_dotenv", lambda *args, **kwargs: None)

    def carregar():
        return comando.FluxoManual(
            service_factory=lambda: instancia,
            dry_run=dry_run,
            envio_habilitado=gate,
            max_lancamentos=int(
                comando.os.environ["OCORRENCIA_73_MAX_LANCAMENTOS"]
            ),
            ctrc_teste=comando.os.environ["OCORRENCIA_73_CTRC_TESTE"],
        )

    monkeypatch.setattr(comando, "_carregar_fluxo_manual", carregar)
    return instancia


def test_scheduler_desligado_nao_e_habilitado_nem_iniciado(monkeypatch):
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "false")
    instancia = preparar(monkeypatch)

    assert comando.main(["--ctrc", "CWB123456"]) == 0
    assert comando.os.environ["OCORRENCIA_73_ENABLED"] == "false"
    assert instancia.chamadas == [{"triggered_by": "homologacao_manual"}]
    fonte = comando.Path(comando.__file__).read_text(encoding="utf-8")
    assert "iniciar_scheduler_ocorrencia_73" not in fonte
    assert "OCORRENCIA_73_ENABLED" not in fonte
    assert "lancar_ocorrencia_73" not in fonte


def test_falta_de_ctrc_explicito_e_rejeitada(monkeypatch):
    preparar(monkeypatch)
    with pytest.raises(SystemExit) as erro:
        comando.main([])
    assert erro.value.code == 2


def test_multiplos_ctrcs_sao_rejeitados(monkeypatch):
    preparar(monkeypatch)
    with pytest.raises(SystemExit) as erro:
        comando.main(["--ctrc", "CWB123", "--ctrc", "BIG456"])
    assert erro.value.code == 2


@pytest.mark.parametrize("valor", ["CWB123,BIG456", "CWB123-1", "123456", "CWB"])
def test_formato_nao_permite_embutir_outro_ctrc(valor, monkeypatch):
    preparar(monkeypatch)
    with pytest.raises(SystemExit):
        comando.main(["--ctrc", valor])


def test_dry_run_permanece_bloqueio_e_nao_exige_autorizacao_real(monkeypatch):
    monkeypatch.delenv(comando.VAR_AUTORIZACAO_MANUAL, raising=False)
    instancia = preparar(monkeypatch, dry_run=True, gate=True)

    assert comando.main(["--ctrc", "CWB123456"]) == 0
    assert len(instancia.chamadas) == 1


def test_gate_desligado_permanece_bloqueio(monkeypatch):
    monkeypatch.delenv(comando.VAR_AUTORIZACAO_MANUAL, raising=False)
    instancia = preparar(monkeypatch, dry_run=False, gate=False)

    assert comando.main(["--ctrc", "CWB123456"]) == 0
    assert len(instancia.chamadas) == 1


def test_modo_potencialmente_real_exige_autorizacao_separada(monkeypatch):
    monkeypatch.delenv(comando.VAR_AUTORIZACAO_MANUAL, raising=False)
    instancia = preparar(monkeypatch, dry_run=False, gate=True)

    with pytest.raises(RuntimeError, match="não autorizada"):
        comando.main(["--ctrc", "CWB123456"])
    assert instancia.chamadas == []


def test_argumento_forca_ctrc_unico_e_limite_um_sem_alterar_gates(monkeypatch):
    monkeypatch.setenv("OCORRENCIA_73_MAX_LANCAMENTOS", "99")
    monkeypatch.setenv("OCORRENCIA_73_DRY_RUN", "true")
    monkeypatch.setenv("OC73_ENVIO_HABILITADO", "NAO")
    preparar(monkeypatch, dry_run=True, gate=False)

    comando.main(["--ctrc", "cwb123456"])

    assert comando.os.environ["OCORRENCIA_73_CTRC_TESTE"] == "CWB123456"
    assert comando.os.environ["OCORRENCIA_73_MAX_LANCAMENTOS"] == "1"
    assert comando.os.environ["OCORRENCIA_73_DRY_RUN"] == "true"
    assert comando.os.environ["OC73_ENVIO_HABILITADO"] == "NAO"


@pytest.mark.parametrize(
    "resultado",
    [
        {"itens": [{"status": "bloqueado_reserva_existente"}]},
        {"itens": [{"status": "incerto"}]},
    ],
)
def test_reserva_duplicada_ou_incerta_nao_dispara_segunda_execucao(
    monkeypatch, resultado
):
    instancia = preparar(monkeypatch, resultado=resultado)

    comando.main(["--ctrc", "CWB123456"])

    assert len(instancia.chamadas) == 1


def test_testes_usam_service_fake_sem_cliente_http(monkeypatch):
    instancia = preparar(monkeypatch)
    comando.main(["--ctrc", "CWB123456"])
    assert isinstance(instancia, ServiceFake)
    assert not hasattr(instancia, "client")
