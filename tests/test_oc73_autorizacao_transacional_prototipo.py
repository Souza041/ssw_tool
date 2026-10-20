"""Cenários offline da arquitetura transacional experimental OC73."""

from __future__ import annotations

import ast
import pickle
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from prototypes.oc73_autorizacao_transacional import (
    ArmazenamentoTransacionalFalso,
    AutorizacaoHomologacao,
    AutorizacaoNegada,
    CommitAmbiguo,
    ConfiguracaoMySQLLaboratorio,
    ConflitoPersistente,
    FalhaTransacional,
    IdentidadeAutenticada,
    PAPEL_OPERACIONAL,
    PAPEL_TECNICO,
    ProcessoInterrompido,
    TentativaHomologacao,
    executar_homologacao,
    validar_configuracao_mysql_laboratorio,
)


AGORA = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
REFERENCIA = "OC73-A1B2C3D4E5F60718293A"


def identidade(nome, papel, **mudancas):
    dados = {
        "issuer": "https://idp.corporativo.invalid",
        "subject_id": f"subject:{nome}",
        "person_issuer": "urn:diretorio-corporativo:person",
        "person_id": f"person:{nome}",
        "person_verified": True,
        "username": nome,
        "authenticated": True,
        "roles": frozenset({papel}),
        "provider": "idp-corporativo-teste",
    }
    dados.update(mudancas)
    return IdentidadeAutenticada(**dados)


def tentativa(**mudancas):
    dados = {
        "tentativa_id": 701,
        "serie": "CWB",
        "numero": "100001",
        "seq_ctrc": "900001",
        "familia": "ROD",
        "local": "CWB",
        "referencia": REFERENCIA,
        "revisao_codigo": "abcdef1234567890",
        "status": "reservado",
    }
    dados.update(mudancas)
    return TentativaHomologacao(**dados)


def autorizacao(**mudancas):
    base = tentativa()
    dados = {
        "autorizacao_id": "auth-001",
        "tentativa_id": base.tentativa_id,
        "serie": base.serie,
        "numero": base.numero,
        "seq_ctrc": base.seq_ctrc,
        "familia": base.familia,
        "local": base.local,
        "referencia": base.referencia,
        "revisao_codigo": base.revisao_codigo,
        "emitida_em": AGORA - timedelta(minutes=1),
        "expira_em": AGORA + timedelta(minutes=4),
        "aprovador_tecnico": identidade("tecnico", PAPEL_TECNICO),
        "aprovador_operacional": identidade(
            "operacional", PAPEL_OPERACIONAL
        ),
    }
    dados.update(mudancas)
    return AutorizacaoHomologacao(**dados)


def armazenamento_pronto(*, tentativa_obj=None, autorizacao_obj=None):
    store = ArmazenamentoTransacionalFalso()
    store.cadastrar_tentativa(tentativa_obj or tentativa())
    store.cadastrar_autorizacao(autorizacao_obj or autorizacao())
    return store


def estado(store):
    return store.snapshot()


class _ArquivoCompartilhado:
    def __init__(self, caminho):
        self.caminho = Path(caminho)

    def __contains__(self, chave):
        return self.caminho.exists() and chave in self._ler()

    def __getitem__(self, chave):
        return self._ler()[chave]

    def __setitem__(self, chave, valor):
        dados = self._ler() if self.caminho.exists() else {}
        dados[chave] = valor
        self.caminho.write_bytes(pickle.dumps(dados))

    def _ler(self):
        return pickle.loads(self.caminho.read_bytes())


class _LockArquivoWindows:
    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.arquivo = None

    def __enter__(self):
        import msvcrt

        self.caminho.touch(exist_ok=True)
        self.arquivo = self.caminho.open("r+b")
        if self.caminho.stat().st_size == 0:
            self.arquivo.write(b"0")
            self.arquivo.flush()
        self.arquivo.seek(0)
        msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, exc_type, exc, traceback):
        import msvcrt

        self.arquivo.seek(0)
        msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_UNLCK, 1)
        self.arquivo.close()


def _consumir_compartilhado(caminho_estado, caminho_lock):
    store = ArmazenamentoTransacionalFalso(
        _ArquivoCompartilhado(caminho_estado),
        _LockArquivoWindows(caminho_lock),
    )
    try:
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)
        return "commit"
    except AutorizacaoNegada:
        return "bloqueada"


def test_mesma_autorizacao_em_dois_processos_tem_um_commit(tmp_path):
    contexto = get_context("spawn")
    caminho_estado = tmp_path / "estado.pickle"
    caminho_lock = tmp_path / "estado.lock"
    store = ArmazenamentoTransacionalFalso(
        _ArquivoCompartilhado(caminho_estado),
        _LockArquivoWindows(caminho_lock),
    )
    store.cadastrar_tentativa(tentativa())
    store.cadastrar_autorizacao(autorizacao())
    with ProcessPoolExecutor(2, mp_context=contexto) as executor:
        futuros = [
            executor.submit(
                _consumir_compartilhado,
                str(caminho_estado),
                str(caminho_lock),
            )
            for _ in range(2)
        ]
        resultados = [item.result(timeout=20) for item in futuros]
    assert sorted(resultados) == ["bloqueada", "commit"]
    assert estado(store)["tentativas"][701]["status"] == "envio_iniciado"


def test_duas_autorizacoes_diferentes_para_mesma_tentativa_sao_bloqueadas():
    store = armazenamento_pronto()
    with pytest.raises(ConflitoPersistente):
        store.cadastrar_autorizacao(
            autorizacao(autorizacao_id="auth-002")
        )


def test_duas_autorizacoes_concorrentes_para_mesma_tentativa_tem_uma_vencedora():
    store = ArmazenamentoTransacionalFalso()
    store.cadastrar_tentativa(tentativa())

    def cadastrar(numero):
        try:
            store.cadastrar_autorizacao(
                autorizacao(autorizacao_id=f"auth-{numero:03d}")
            )
            return "criada"
        except ConflitoPersistente:
            return "bloqueada"

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(cadastrar, (1, 2)))
    assert sorted(resultados) == ["bloqueada", "criada"]
    assert len(estado(store)["autorizacoes"]) == 1


def test_dois_ids_de_tentativa_para_mesmo_ctrc_sao_bloqueados():
    store = armazenamento_pronto()
    with pytest.raises(ConflitoPersistente):
        store.cadastrar_tentativa(tentativa(tentativa_id=702))


def test_autorizacao_expirada_falha_fechado():
    auth = autorizacao(
        emitida_em=AGORA - timedelta(minutes=10),
        expira_em=AGORA - timedelta(seconds=1),
    )
    store = armazenamento_pronto(autorizacao_obj=auth)
    with pytest.raises(AutorizacaoNegada, match="expirada"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_prazo_superior_a_quinze_minutos_e_rejeitado_no_cadastro():
    store = ArmazenamentoTransacionalFalso()
    store.cadastrar_tentativa(tentativa())
    with pytest.raises(AutorizacaoNegada, match="15 minutos"):
        store.cadastrar_autorizacao(
            autorizacao(
                emitida_em=AGORA,
                expira_em=AGORA + timedelta(minutes=16),
            )
        )


def test_autorizacao_revogada_falha_fechado():
    store = armazenamento_pronto()
    store.revogar("auth-001", AGORA)
    with pytest.raises(AutorizacaoNegada):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("referencia", "OC73-FFFFFFFFFFFFFFFFFFFF"),
        ("revisao_codigo", "1234567abcdef"),
    ],
)
def test_referencia_ou_revisao_divergente_bloqueia(campo, valor):
    store = armazenamento_pronto(
        autorizacao_obj=autorizacao(**{campo: valor})
    )
    with pytest.raises(AutorizacaoNegada, match="diverge"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_mesma_conta_nos_dois_papeis_bloqueia():
    mesma = identidade(
        "mesmo",
        PAPEL_TECNICO,
        roles=frozenset({PAPEL_TECNICO, PAPEL_OPERACIONAL}),
    )
    store = armazenamento_pronto(
        autorizacao_obj=autorizacao(
            aprovador_tecnico=mesma, aprovador_operacional=mesma
        )
    )
    with pytest.raises(AutorizacaoNegada, match="contas distintas"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_mesma_pessoa_com_contas_diferentes_bloqueia():
    tecnico = identidade("a", PAPEL_TECNICO, person_id="person:1")
    operacional = identidade(
        "b", PAPEL_OPERACIONAL, person_id="person:1"
    )
    store = armazenamento_pronto(
        autorizacao_obj=autorizacao(
            aprovador_tecnico=tecnico,
            aprovador_operacional=operacional,
        )
    )
    with pytest.raises(AutorizacaoNegada, match="pessoas distintas"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_identidade_nao_autenticada_bloqueia():
    auth = autorizacao(
        aprovador_tecnico=identidade(
            "tecnico", PAPEL_TECNICO, authenticated=False
        )
    )
    store = armazenamento_pronto(autorizacao_obj=auth)
    with pytest.raises(AutorizacaoNegada, match="não autenticada"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_papel_insuficiente_bloqueia():
    auth = autorizacao(
        aprovador_tecnico=identidade(
            "tecnico", PAPEL_TECNICO, roles=frozenset()
        )
    )
    store = armazenamento_pronto(autorizacao_obj=auth)
    with pytest.raises(AutorizacaoNegada, match="insuficiente"):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_falha_de_auditoria_reverte_toda_transacao():
    store = armazenamento_pronto()
    antes = estado(store)
    with pytest.raises(FalhaTransacional):
        store.consumir_e_iniciar_envio(
            "auth-001", 701, AGORA, falha="auditoria"
        )
    assert estado(store) == antes


def test_falha_entre_updates_condicionais_reverte_snapshot_inteiro():
    store = armazenamento_pronto()
    antes = estado(store)
    with pytest.raises(FalhaTransacional):
        store.consumir_e_iniciar_envio(
            "auth-001", 701, AGORA, falha="entre_updates"
        )
    assert estado(store) == antes


@pytest.mark.parametrize(
    "falha", ["antes_commit", "deadlock", "timeout_trava", "conexao_perdida"]
)
def test_falhas_antes_do_commit_nao_mudam_estado_nem_invocam_callback(falha):
    store = armazenamento_pronto()
    antes = estado(store)
    chamadas = []
    with pytest.raises(FalhaTransacional):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=lambda permissao: chamadas.append(permissao),
            falha=falha,
        )
    assert estado(store) == antes
    assert chamadas == []


def test_commit_ambiguo_sem_confirmacao_nao_invoca_callback():
    store = armazenamento_pronto()
    chamadas = []
    with pytest.raises(CommitAmbiguo):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=lambda permissao: chamadas.append(permissao),
            falha="commit_ambiguo_nao_confirmado",
        )
    assert chamadas == []
    assert estado(store)["tentativas"][701]["status"] == "reservado"


def test_commit_ambiguo_possivelmente_confirmado_nao_invoca_nem_reabre():
    store = armazenamento_pronto()
    chamadas = []
    with pytest.raises(CommitAmbiguo):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=lambda permissao: chamadas.append(permissao),
            falha="commit_ambiguo_confirmado",
        )
    assert chamadas == []
    assert estado(store)["tentativas"][701]["status"] == "envio_iniciado"
    with pytest.raises(AutorizacaoNegada):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_queda_apos_commit_antes_do_post_bloqueia_repeticao():
    store = armazenamento_pronto()
    chamadas = []
    with pytest.raises(ProcessoInterrompido):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=lambda permissao: chamadas.append(permissao),
            falha="apos_commit_antes_callback",
        )
    assert chamadas == []
    assert estado(store)["tentativas"][701]["status"] == "envio_iniciado"
    with pytest.raises(AutorizacaoNegada):
        store.consumir_e_iniciar_envio("auth-001", 701, AGORA)


def test_timeout_do_post_falso_marca_incerto_e_nao_reenvia():
    store = armazenamento_pronto()
    chamadas = []

    def timeout(permissao):
        chamadas.append(permissao)
        raise TimeoutError("timeout falso")

    with pytest.raises(TimeoutError):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=timeout,
        )
    assert len(chamadas) == 1
    assert estado(store)["tentativas"][701]["status"] == "incerto"
    with pytest.raises(AutorizacaoNegada):
        executar_homologacao(
            store,
            autorizacao_id="auth-001",
            tentativa_id=701,
            agora=AGORA,
            callback=timeout,
        )
    assert len(chamadas) == 1


def test_callback_so_recebe_permissao_depois_do_commit():
    store = armazenamento_pronto()

    def callback(permissao):
        atual = estado(store)
        assert atual["tentativas"][701]["status"] == "envio_iniciado"
        assert atual["autorizacoes"]["auth-001"]["status"] == "consumida"
        assert permissao.commit_seq == atual["commit_seq"]
        return "fake-ok"

    resultado = executar_homologacao(
        store,
        autorizacao_id="auth-001",
        tentativa_id=701,
        agora=AGORA,
        callback=callback,
    )
    assert resultado.callback_invocado is True
    assert resultado.resposta == "fake-ok"


def test_prototipo_permanece_isolado_de_runtime_rede_e_mysql():
    caminho = Path("prototypes/oc73_autorizacao_transacional.py")
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importacoes = {
        alias.name
        for no in ast.walk(arvore)
        if isinstance(no, ast.Import)
        for alias in no.names
    }
    importacoes.update(
        no.module
        for no in ast.walk(arvore)
        if isinstance(no, ast.ImportFrom) and no.module
    )
    proibidos = ("modules", "operations", "web", "ssw", "pymysql", "requests")
    assert not any(item.startswith(proibidos) for item in importacoes)


def test_runtime_nao_importa_novo_prototipo():
    for caminho in Path("modules/ocorrencia_73").glob("*.py"):
        assert "oc73_autorizacao_transacional" not in caminho.read_text(
            encoding="utf-8"
        )


def test_modo_mysql_recusa_sem_flag_e_fora_da_allowlist_literal():
    base = dict(
        host="127.0.0.1",
        porta=3308,
        schema="oc73_autorizacao_transacional_test",
        usuario="oc73_tester",
        server_uuid_esperado="uuid-laboratorio",
        flag_explicita=False,
    )
    with pytest.raises(AutorizacaoNegada):
        validar_configuracao_mysql_laboratorio(
            ConfiguracaoMySQLLaboratorio(**base)
        )
    base["flag_explicita"] = True
    base["schema"] = "automationx"
    with pytest.raises(AutorizacaoNegada):
        validar_configuracao_mysql_laboratorio(
            ConfiguracaoMySQLLaboratorio(**base)
        )


def test_modo_mysql_aceita_somente_configuracao_literal_com_uuid_explicito():
    validar_configuracao_mysql_laboratorio(
        ConfiguracaoMySQLLaboratorio(
            host="127.0.0.1",
            porta=3308,
            schema="oc73_autorizacao_transacional_test",
            usuario="oc73_tester",
            server_uuid_esperado="uuid-laboratorio-autorizado",
            flag_explicita=True,
        )
    )
