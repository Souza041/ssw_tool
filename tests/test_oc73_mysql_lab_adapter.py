"""Testes offline da fábrica de conexão; o driver é sempre falso."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from prototypes.oc73_laboratorio_verificador import ConfiguracaoLaboratorio
from prototypes.oc73_mysql_lab_adapter import (
    ConexaoLaboratorioErro,
    ContratoDriver,
    CredencialLaboratorio,
    ParametrosConexaoAuditados,
    construir_fabrica_conexao,
)


def contrato_driver(**mudancas):
    dados = {
        "pacote": "PyMySQL",
        "versao_esperada": "versao-sintetica-inventariada",
        "origem": "indice-interno-sintetico",
        "integridade_sha256": "a" * 64,
        "capabilities": frozenset({
            "dict_cursor", "timeouts", "client_flag_zero"
        }),
        "inventario_confirmado": True,
    }
    dados.update(mudancas)
    return ContratoDriver(**dados)


def config():
    return ConfiguracaoLaboratorio(
        host="127.0.0.1",
        porta=3308,
        schema="oc73_autorizacao_transacional_test",
        usuario="oc73_tester",
        conta_efetiva_esperada="oc73_tester@127.0.0.1",
        usuario_conexao_esperado="oc73_tester@127.0.0.1",
        server_uuid_esperado="11111111-2222-4333-8444-555555555555",
        datadir_esperado="C:/mysql57-lab/data/",
        versao_esperada="5.7.32-log",
        distribuicao_esperada="MySQL Community Server (GPL)",
        hostname_esperado="mysql57-lab",
        perfil_privilegios="transacional",
        autorizacao_explicita=True,
        unix_socket=None,
    )


class DriverFalso:
    def __init__(self, *, falhar=False):
        self.chamadas = []
        self.falhar = falhar
        self.cursors = SimpleNamespace(DictCursor=object())

    def connect(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.falhar:
            raise OSError(f"password={kwargs['password']}")
        return object()


def test_construir_fabrica_nao_conecta_e_chamada_usa_parametros_exatos():
    driver = DriverFalso()
    fabrica = construir_fabrica_conexao(
        config=config(),
        credencial=CredencialLaboratorio("segredo-de-teste"),
        contrato_driver=contrato_driver(),
        driver=driver,
    )
    assert driver.chamadas == []
    fabrica()
    assert len(driver.chamadas) == 1
    opcoes = driver.chamadas[0]
    assert opcoes == {
        "host": "127.0.0.1",
        "port": 3308,
        "user": "oc73_tester",
        "password": "segredo-de-teste",
        "database": "oc73_autorizacao_transacional_test",
        "charset": "utf8mb4",
        "cursorclass": driver.cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 5,
        "read_timeout": 5,
        "write_timeout": 5,
        "client_flag": 0,
    }
    assert "unix_socket" not in opcoes


def test_erro_do_driver_e_sanitizado_sem_senha_ou_dsn():
    driver = DriverFalso(falhar=True)
    fabrica = construir_fabrica_conexao(
        config=config(),
        credencial=CredencialLaboratorio("segredo-ultrassecreto"),
        contrato_driver=contrato_driver(),
        driver=driver,
    )
    with pytest.raises(ConexaoLaboratorioErro) as capturada:
        fabrica()
    mensagem = str(capturada.value)
    assert "segredo-ultrassecreto" not in mensagem
    assert "password" not in mensagem
    assert "127.0.0.1" not in mensagem
    assert capturada.value.__cause__ is None


@pytest.mark.parametrize(
    "parametros",
    [
        ParametrosConexaoAuditados(client_flag=65536),
        ParametrosConexaoAuditados(autocommit=True),
        ParametrosConexaoAuditados(connect_timeout=0),
        ParametrosConexaoAuditados(read_timeout=31),
        ParametrosConexaoAuditados(charset="latin1"),
    ],
)
def test_parametro_de_driver_inseguro_e_rejeitado_antes_de_conectar(parametros):
    driver = DriverFalso()
    with pytest.raises(ConexaoLaboratorioErro):
        construir_fabrica_conexao(
            config=config(),
            credencial=CredencialLaboratorio("segredo"),
            contrato_driver=contrato_driver(),
            driver=driver,
            parametros=parametros,
        )
    assert driver.chamadas == []


def test_parametro_inesperado_na_fabrica_e_rejeitado_pela_assinatura():
    with pytest.raises(TypeError):
        construir_fabrica_conexao(
            config=config(),
            credencial=CredencialLaboratorio("segredo"),
            contrato_driver=contrato_driver(),
            driver=DriverFalso(),
            redirecionar_host=True,
        )


def test_driver_sem_inventario_ou_hash_e_bloqueado():
    driver = DriverFalso()
    with pytest.raises(ConexaoLaboratorioErro):
        construir_fabrica_conexao(
            config=config(),
            credencial=CredencialLaboratorio("segredo"),
            contrato_driver=contrato_driver(inventario_confirmado=False),
            driver=driver,
        )
    assert driver.chamadas == []


def test_fabrica_congela_opcoes_e_nao_rele_configuracao_por_referencia():
    driver = DriverFalso()
    config_validada = config()
    credencial = CredencialLaboratorio("segredo-lab")
    fabrica = construir_fabrica_conexao(
        config=config_validada,
        credencial=credencial,
        contrato_driver=contrato_driver(),
        driver=driver,
    )

    # Mesmo uma mutação deliberada que contorne o frozen dataclass não pode
    # redirecionar a closure já construída para outro endpoint ou segredo.
    object.__setattr__(
        config_validada, "host", "automationx.mysql.dbaas.com.br"
    )
    object.__setattr__(credencial, "senha", "segredo-alterado")
    fabrica()

    assert driver.chamadas[0]["host"] == "127.0.0.1"
    assert driver.chamadas[0]["password"] == "segredo-lab"


def test_modulo_nao_carrega_env_driver_runtime_nem_conecta_na_importacao():
    caminho = Path("prototypes/oc73_mysql_lab_adapter.py")
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
    proibidos = (
        "os", "dotenv", "pymysql", "socket", "modules", "operations",
        "web", "ssw",
    )
    assert not any(nome.startswith(proibidos) for nome in importacoes)
    chamadas_connect = [
        no for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Name)
        and no.func.id == "connect"
    ]
    assert len(chamadas_connect) == 1
    pais = {
        filho: pai
        for pai in ast.walk(arvore)
        for filho in ast.iter_child_nodes(pai)
    }
    atual = chamadas_connect[0]
    ancestrais = []
    while atual in pais:
        atual = pais[atual]
        ancestrais.append(atual)
    assert any(
        isinstance(no, ast.FunctionDef) and no.name == "conectar"
        for no in ancestrais
    )
