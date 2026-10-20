"""Testes offline do protótipo; nenhuma rede ou persistência operacional."""

from __future__ import annotations

import ast
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from modules.ocorrencia_73.gate import PortaoOC73
from operations.op101.ocorrencias import OP101Ocorrencias
from prototypes.oc73_homologacao_supervisionada import (
    ArmazenamentoUsoUnicoLocal,
    AutorizacaoDivergente,
    AutorizacaoExpirada,
    AutorizacaoInvalida,
    AutorizacaoReutilizada,
    AutorizacaoRevogada,
    ConteudoAutorizacao,
    ContextoTentativa,
    FalhaConsumoAutorizacao,
    SondaPostOffline,
    TransporteSSWFalsoInstrumentado,
    emitir_token,
    validar_e_consumir,
)


AGORA = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
CHAVE = b"chave-offline-de-teste-com-mais-de-32-bytes"
REFERENCIA = "OC73-A1B2C3D4E5F60718293A"
REVISAO = "abcdef1234567890"
AUTORIZACAO_ID = "0123456789abcdef0123456789abcdef"


def criar_conteudo(**alteracoes):
    dados = {
        "autorizacao_id": AUTORIZACAO_ID,
        "serie": "CWB",
        "numero": "100001",
        "seq_ctrc": "900001",
        "familia": "ROD",
        "local": "CWB",
        "referencia": REFERENCIA,
        "revisao_codigo": REVISAO,
        "emitida_em": AGORA - timedelta(minutes=1),
        "expira_em": AGORA + timedelta(minutes=4),
        "aceite_tecnico": "RESP-TECNICO",
        "aceite_operacional": "RESP-OPERACIONAL",
    }
    dados.update(alteracoes)
    return ConteudoAutorizacao.criar(**dados)


def criar_contexto(**alteracoes):
    dados = {
        "serie": "CWB",
        "numero": "100001",
        "seq_ctrc": "900001",
        "familia": "ROD",
        "local": "CWB",
        "referencia": REFERENCIA,
        "revisao_codigo": REVISAO,
        "status_persistido": "reservado",
    }
    dados.update(alteracoes)
    return ContextoTentativa(**dados)


def consumir(token, diretorio: Path, contexto=None, armazenamento=None):
    return validar_e_consumir(
        token=token,
        chave=CHAVE,
        contexto=contexto or criar_contexto(),
        armazenamento=(
            armazenamento or ArmazenamentoUsoUnicoLocal(diretorio)
        ),
        agora=AGORA,
    )


def test_prototipo_nao_importa_runtime_rede_ou_banco():
    caminho = Path(
        "prototypes/oc73_homologacao_supervisionada.py"
    )
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importacoes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importacoes.update(alias.name for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importacoes.add(no.module)

    prefixos_proibidos = (
        "modules",
        "operations",
        "ssw",
        "web",
        "pymysql",
        "requests",
        "httpx",
    )
    assert not any(
        modulo.startswith(prefixos_proibidos)
        for modulo in importacoes
    )


def test_runtime_nao_importa_prototipo():
    arquivos_runtime = (
        Path("modules/ocorrencia_73/service.py"),
        Path("modules/ocorrencia_73/gate.py"),
        Path("modules/ocorrencia_73/repository.py"),
        Path("modules/ocorrencia_73/scheduler.py"),
        Path("operations/op101/ocorrencias.py"),
        Path("web/main.py"),
    )
    for caminho in arquivos_runtime:
        assert "prototypes.oc73_homologacao" not in caminho.read_text(
            encoding="utf-8"
        )


def _consumir_em_processo(token, diretorio, contexto_dados):
    try:
        validar_e_consumir(
            token=token,
            chave=CHAVE,
            contexto=ContextoTentativa(**contexto_dados),
            armazenamento=ArmazenamentoUsoUnicoLocal(Path(diretorio)),
            agora=AGORA,
        )
        return "consumida"
    except AutorizacaoReutilizada:
        return "reutilizada"


def test_ausencia_de_autorizacao_falha_fechado(tmp_path):
    with pytest.raises(AutorizacaoInvalida):
        consumir(None, tmp_path)


def test_autorizacao_expirada_falha_fechado(tmp_path):
    conteudo = criar_conteudo(
        emitida_em=AGORA - timedelta(minutes=10),
        expira_em=AGORA - timedelta(minutes=1),
    )
    with pytest.raises(AutorizacaoExpirada):
        consumir(emitir_token(conteudo, CHAVE), tmp_path)


@pytest.mark.parametrize(
    "alteracao",
    [
        {"serie": "BIG"},
        {"numero": "100002"},
        {"seq_ctrc": "900002"},
        {"familia": "OUTRA"},
        {"local": "BIG"},
    ],
)
def test_ctrc_ou_identificador_diferente_bloqueia(tmp_path, alteracao):
    token = emitir_token(criar_conteudo(), CHAVE)
    with pytest.raises(AutorizacaoDivergente):
        consumir(token, tmp_path, criar_contexto(**alteracao))


def test_referencia_diferente_bloqueia(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    with pytest.raises(AutorizacaoDivergente):
        consumir(
            token,
            tmp_path,
            criar_contexto(referencia="OC73-FFFFFFFFFFFFFFFFFFFF"),
        )


def test_revisao_de_codigo_diferente_bloqueia(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    with pytest.raises(AutorizacaoDivergente):
        consumir(
            token,
            tmp_path,
            criar_contexto(revisao_codigo="1234567abcdef"),
        )


def test_apenas_um_aceite_bloqueia_emissao():
    with pytest.raises(AutorizacaoInvalida, match="aceite_operacional"):
        criar_conteudo(aceite_operacional="")


def test_dois_aceites_da_mesma_identidade_bloqueiam_emissao():
    with pytest.raises(AutorizacaoInvalida, match="distintos"):
        criar_conteudo(aceite_operacional="resp-tecnico")


def test_alteracao_do_conteudo_apos_assinatura_invalida_token(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    token["payload"]["numero"] = "999999"
    with pytest.raises(AutorizacaoInvalida, match="Assinatura"):
        consumir(token, tmp_path)


def test_autorizacao_so_pode_ser_consumida_uma_vez(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    assert consumir(token, tmp_path) == AUTORIZACAO_ID
    with pytest.raises(AutorizacaoReutilizada):
        consumir(token, tmp_path)


def test_duas_tentativas_em_processos_distintos_consumem_uma_vez(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    contexto_dados = criar_contexto().__dict__
    contexto_mp = get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=2,
        mp_context=contexto_mp,
    ) as executor:
        futuros = [
            executor.submit(
                _consumir_em_processo,
                token,
                str(tmp_path),
                contexto_dados,
            )
            for _ in range(2)
        ]
        resultados = [futuro.result(timeout=20) for futuro in futuros]

    assert sorted(resultados) == ["consumida", "reutilizada"]


def test_falha_durante_consumo_mantem_autorizacao_queimada(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)

    def falhar():
        raise OSError("falha offline injetada")

    armazenamento = ArmazenamentoUsoUnicoLocal(
        tmp_path,
        apos_reivindicar=falhar,
    )
    with pytest.raises(FalhaConsumoAutorizacao):
        consumir(token, tmp_path, armazenamento=armazenamento)
    with pytest.raises(AutorizacaoReutilizada):
        consumir(token, tmp_path)


def test_revogacao_persistida_bloqueia_consumo(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    armazenamento = ArmazenamentoUsoUnicoLocal(tmp_path)

    assert armazenamento.revogar(AUTORIZACAO_ID, agora=AGORA) is True
    with pytest.raises(AutorizacaoRevogada):
        consumir(token, tmp_path, armazenamento=armazenamento)


def test_falha_apos_consumo_antes_do_post_nao_reabre_autorizacao(tmp_path):
    token = emitir_token(criar_conteudo(), CHAVE)
    consumir(token, tmp_path)

    # Simula a janela conservadora: autorização já queimada e nenhum POST.
    sonda = SondaPostOffline()
    assert sonda.contadores.tentativa_transporte == 0
    with pytest.raises(AutorizacaoReutilizada):
        consumir(token, tmp_path)


@pytest.mark.parametrize(
    "status",
    ["envio_iniciado", "incerto", "confirmado"],
)
def test_estados_inseguros_nao_podem_consumir_autorizacao(tmp_path, status):
    token = emitir_token(criar_conteudo(), CHAVE)
    with pytest.raises(AutorizacaoDivergente, match="Estado"):
        consumir(token, tmp_path, criar_contexto(status_persistido=status))


class RepositorioFalso:
    def __init__(self):
        self.status = None
        self.confirmacoes = 0

    def reservar_ctrc(self, **kwargs):
        criada = self.status is None
        if criada:
            self.status = "reservado"
        return {
            "id": 701,
            "referencia": REFERENCIA,
            "status": self.status,
            "execucao_id": 301,
            "reserva_criada": criada,
        }

    def iniciar_envio(self, tentativa_id, execucao_id):
        if self.status != "reservado":
            return False
        self.status = "envio_iniciado"
        return True

    def marcar_incerto(self, *, tentativa_id, mensagem_erro):
        assert self.status == "envio_iniciado"
        self.status = "incerto"

    def confirmar_lancamento(self, *, tentativa_id, resposta_ssw):
        assert self.status == "envio_iniciado"
        self.status = "confirmado"
        self.confirmacoes += 1


def processar_com_sonda(repository, transporte, sonda, *, callback=None):
    op101 = OP101Ocorrencias(transporte)

    def enviar(referencia):
        acao = callback or (
            lambda: op101.lancar_ocorrencia_73(
                seq_ctrc="900001",
                familia="ROD",
                data_ocorrencia=date(2026, 10, 16),
                observacao=f"BOT OC73 | REF:{referencia}",
                local="CWB",
            )
        )
        return sonda.solicitar_callback(acao)

    def confirmar(referencia):
        ocorrencia = op101.confirmar_ocorrencia_73(
            serie="CWB",
            numero="100001",
            seq_ctrc="900001",
            local="CWB",
            familia="ROD",
            data_referencia=date(2026, 10, 16),
            referencia=referencia,
        )
        sonda.registrar_confirmacao_historico(ocorrencia is not None)
        return ocorrencia

    return PortaoOC73(repository).processar(
        execucao_id=301,
        serie="CWB",
        numero="100001",
        fluxo="CWB",
        data_inicial=date(2026, 10, 16),
        data_final=date(2026, 10, 16),
        enviar=enviar,
        confirmar=confirmar,
    )


def historico(referencia=REFERENCIA):
    return (
        "<historico><r><f5>73 - ENTREGA SERA REALIZADA AMANHA</f5>"
        f"<f6>BOT OC73 | REF:{referencia}</f6></r></historico>"
    )


def test_timeout_apos_transporte_aceitar_nao_provoca_segundo_post():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(
        sonda,
        modo_lancamento="timeout_apos_aceite",
    )

    primeiro = processar_com_sonda(repo, transporte, sonda)
    segundo = processar_com_sonda(repo, transporte, sonda)

    assert primeiro.status == "incerto"
    assert segundo.status == "bloqueado_reserva_existente"
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 1
    assert sonda.contadores.observada_transporte_falso == 1
    assert sonda.contadores.resposta_recebida == 0
    assert sonda.contadores.confirmada_por_historico == 0


def test_resposta_ambigua_sem_referencia_nao_confirma():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(
        sonda,
        modo_lancamento="ambiguo",
        historico="<historico></historico>",
    )

    resultado = processar_com_sonda(repo, transporte, sonda)

    assert resultado.status == "incerto"
    assert repo.confirmacoes == 0
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 1
    assert sonda.contadores.observada_transporte_falso == 1
    assert sonda.contadores.resposta_recebida == 1
    assert sonda.contadores.confirmada_por_historico == 0


def test_referencia_truncada_no_historico_nao_confirma():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(
        sonda,
        historico=historico(REFERENCIA[:-1]),
    )

    resultado = processar_com_sonda(repo, transporte, sonda)

    assert resultado.status == "incerto"
    assert repo.confirmacoes == 0
    assert sonda.contadores.observada_transporte_falso == 1
    assert sonda.contadores.confirmada_por_historico == 0


def test_falha_antes_da_chamada_ao_transporte_e_distinguida():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(sonda)

    def falhar_antes():
        raise ConnectionError("falha antes do transporte falso")

    resultado = processar_com_sonda(
        repo,
        transporte,
        sonda,
        callback=falhar_antes,
    )

    assert resultado.status == "incerto"
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 0
    assert sonda.contadores.observada_transporte_falso == 0
    assert sonda.contadores.resposta_recebida == 0


def test_falha_na_entrada_do_transporte_nao_e_aceite_observado():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(
        sonda,
        modo_lancamento="falha_antes_aceite",
    )

    resultado = processar_com_sonda(repo, transporte, sonda)

    assert resultado.status == "incerto"
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 1
    assert sonda.contadores.observada_transporte_falso == 0
    assert sonda.contadores.resposta_recebida == 0


def test_interrupcao_apos_envio_iniciado_nao_reenvia():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(sonda)

    def interromper():
        raise SystemExit("interrupção offline")

    with pytest.raises(SystemExit):
        processar_com_sonda(
            repo,
            transporte,
            sonda,
            callback=interromper,
        )
    assert repo.status == "envio_iniciado"

    segundo = processar_com_sonda(repo, transporte, sonda)
    assert segundo.status == "bloqueado_reserva_existente"
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 0


def test_posts_consultivos_nao_incrementam_contador_de_lancamento():
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(sonda)

    transporte.post("/bin/ssw0230", data={"act": "E1"})
    transporte.post("/bin/ssw0053", data={"act": "P1"})
    transporte.post("/bin/ssw0053", data={"act": "O"})
    transporte.post("/bin/ssw0122", data={"act": "CONSULTA", "f3": "73"})

    assert sonda.contadores.posts_consultivos == 4
    assert sonda.contadores.tentativa_transporte == 0
    assert sonda.contadores.observada_transporte_falso == 0


def test_evento_de_lancamento_e_saneado_sem_payload_sensivel():
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(sonda)
    op101 = OP101Ocorrencias(transporte)

    sonda.solicitar_callback(
        lambda: op101.lancar_ocorrencia_73(
            seq_ctrc="900001",
            familia="ROD",
            data_ocorrencia=date(2026, 10, 16),
            observacao=f"BOT OC73 | REF:{REFERENCIA}",
            local="CWB",
        )
    )

    evento = sonda.eventos_saneados[-1]
    serializado = str(evento)
    assert evento["operacao"] == "lancamento_oc73"
    assert REFERENCIA not in serializado
    assert "900001" not in serializado
    assert "payload" not in evento
    assert sonda.contadores.observada_transporte_falso == 1


def test_confirmacao_por_historico_e_contada_separadamente():
    repo = RepositorioFalso()
    sonda = SondaPostOffline()
    transporte = TransporteSSWFalsoInstrumentado(
        sonda,
        historico=historico(),
    )

    resultado = processar_com_sonda(repo, transporte, sonda)

    assert resultado.status == "confirmado"
    assert sonda.contadores.callback_solicitado == 1
    assert sonda.contadores.tentativa_transporte == 1
    assert sonda.contadores.observada_transporte_falso == 1
    assert sonda.contadores.resposta_recebida == 1
    assert sonda.contadores.confirmada_por_historico == 1
