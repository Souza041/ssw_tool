"""Testes offline: não executam o script PowerShell nem APIs administrativas."""

from copy import deepcopy
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path

import pytest

from prototypes.oc73_inventario_analisador import (
    Classificacao,
    InventarioInvalido,
    ResultadoProcedencia,
    analisar_relatorio,
)


AGORA = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
SCRIPT = Path("scripts/oc73_inventario_windows_readonly.ps1")
DOC_INVENTARIO = Path("docs/oc73_inventario_windows_3_16.md")
DOC_AUTORIZACAO = Path("docs/oc73_autorizacao_inventario_3_16.md")
DOC_HARDENING = Path("docs/oc73_hardening_pre_coleta_3_16_1.md")
DOC_FECHAMENTO = Path("docs/oc73_fechamento_pre_coleta_3_16_2.md")


def relatorio_valido():
    return {
        "schema_version": "oc73-inventario-windows-v2",
        "collected_at": "2026-10-16T14:30:00+00:00",
        "expires_at": "2026-10-16T15:30:00+00:00",
        "host_contract": {
            "host": "127.0.0.1",
            "port": 3308,
            "schema": "oc73_autorizacao_transacional_test",
            "user": "oc73_tester",
            "product": "MySQL Community",
            "version": "5.7.32",
        },
        "os": {"status": "OBSERVADO", "caption": "Windows", "version": "10.0"},
        "services": [{
            "status": "OBSERVADO",
            "name": "MySQL57-OC73-Lab",
            "state": "RUNNING",
            "pid": 4242,
            "executable": "D:\\mysql57-lab\\bin\\mysqld.exe",
        }],
        "processes": [{
            "status": "OBSERVADO",
            "pid": 4242,
            "name": "mysqld.exe",
            "executable": "D:\\mysql57-lab\\bin\\mysqld.exe",
            "product_metadata": "MYSQL_COMMUNITY",
            "version_metadata": "5.7.32",
            "sha256": "a" * 64,
            "signature_status": "VALID",
            "datadir": "D:\\mysql57-lab\\data",
            "volume_id": "volume-oc73-lab",
            "instance_marker": "4242|2026-10-16T14:30:00.0000000Z",
            "stable_observation": True,
            "command_line_summary": {
                "sensitive_arguments_redacted": True,
                "observed_flags": ["port:3308"],
            },
        }],
        "listeners": [{
            "status": "OBSERVADO",
            "local_address": "127.0.0.1",
            "local_port": 3308,
            "pid": 4242,
        }],
        "volumes": [{
            "status": "OBSERVADO",
            "id": "volume-oc73-lab",
        }],
        "shares": [],
        "replication": {
            "status": "NÃO OBSERVADO",
            "indicators": [],
        },
        "errors": [],
    }


def serializar_relatorio(dados):
    return json.dumps(
        dados, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def evidencia_externa_valida(inventario_bytes=None):
    inventario_bytes = inventario_bytes or serializar_relatorio(relatorio_valido())
    return {
        "schema_version": "oc73-administrative-evidence-v2",
        "evidence_id": "EV-OC73-20261016-001",
        "inventory_sha256": hashlib.sha256(inventario_bytes).hexdigest(),
        "instance_id": "oc73-lab-127.0.0.1-3308",
        "responsible": "Administrador autorizado",
        "source": "ADMINISTRATIVE_INSPECTION",
        "observed_at": "2026-10-16T14:40:00+00:00",
        "valid_until": "2026-10-16T16:00:00+00:00",
        "procedure_revision_sha256": "b" * 64,
        "document_reference": "urn:oc73:change:2026-10-16-001",
        "process": {
            "pid": 4242,
            "instance_marker": "4242|2026-10-16T14:30:00.0000000Z",
            "executable_path": "D:\\mysql57-lab\\bin\\mysqld.exe",
            "executable_sha256": "a" * 64,
        },
        "assertions": {
            "product": "MYSQL_COMMUNITY_5_7_32",
            "volume_id": "volume-oc73-lab",
            "volume_dedicated": True,
            "replication_absent": True,
        },
    }


def resultado_procedencia_valido(evidencia, autoridade="urn:oc73:authority:test-only"):
    return ResultadoProcedencia(
        comprovada=True,
        evidence_id=evidencia["evidence_id"],
        inventory_sha256=evidencia["inventory_sha256"],
        procedure_revision_sha256=evidencia["procedure_revision_sha256"],
        authority_reference=autoridade,
    )


def analisar(dados, evidencia=None, verificador=None):
    inventario_bytes = serializar_relatorio(dados)
    evidencia = evidencia or evidencia_externa_valida(inventario_bytes)
    if verificador is None:
        def verificador(item):
            return resultado_procedencia_valido(item)
    return analisar_relatorio(
        dados,
        agora=AGORA,
        evidencia_externa=evidencia,
        verificador_procedencia=verificador,
        inventario_bytes_originais=inventario_bytes,
        revisao_procedimento_aprovada="b" * 64,
    )


def test_porta_3308_ausente_e_indeterminado():
    dados = relatorio_valido()
    dados["listeners"] = []
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_porta_ocupada_por_processo_desconhecido_e_nao_conforme():
    dados = relatorio_valido()
    dados["processes"][0]["name"] = "outro.exe"
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_porta_ocupada_por_mariadb_e_nao_conforme():
    dados = relatorio_valido()
    dados["processes"][0].update(
        name="mariadbd.exe", product_metadata="MARIADB"
    )
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_mysql_com_versao_desconhecida_e_indeterminado():
    dados = relatorio_valido()
    dados["processes"][0]["version_metadata"] = None
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_porta_com_dois_pids_e_nao_conforme():
    dados = relatorio_valido()
    outro = deepcopy(dados["listeners"][0])
    outro["pid"] = 4343
    dados["listeners"].append(outro)
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_pid_inexistente_e_nao_conforme():
    dados = relatorio_valido()
    dados["processes"] = []
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_executavel_do_servico_divergente_e_nao_conforme():
    dados = relatorio_valido()
    dados["services"][0]["executable"] = "D:\\outro\\mysqld.exe"
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_datadir_nao_identificado_e_indeterminado():
    dados = relatorio_valido()
    dados["processes"][0]["datadir"] = None
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_volume_compartilhado_e_nao_conforme():
    dados = relatorio_valido()
    evidencia = evidencia_externa_valida()
    evidencia["assertions"]["volume_dedicated"] = False
    assert analisar(dados, evidencia).classificacao is Classificacao.NAO_CONFORME


def test_xampp_detectado_e_nao_conforme():
    dados = relatorio_valido()
    caminho = "C:\\xampp\\mysql\\bin\\mysqld.exe"
    dados["processes"][0]["executable"] = caminho
    dados["services"][0]["executable"] = caminho
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_indicio_de_replicacao_e_nao_conforme():
    dados = relatorio_valido()
    dados["replication"] = {
        "status": "OBSERVADO",
        "indicators": ["server-id"],
    }
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_replicacao_indeterminada_nao_comprova_ausencia():
    dados = relatorio_valido()
    dados["replication"] = {
        "status": "INDETERMINADO",
        "indicators": [],
    }
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("sha256", None), ("signature_status", "NOT_SIGNED")],
)
def test_hash_ou_assinatura_ausente_e_indeterminado(campo, valor):
    dados = relatorio_valido()
    dados["processes"][0][campo] = valor
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


def test_linha_de_comando_com_segredo_e_rejeitada_sem_vazamento():
    dados = relatorio_valido()
    segredo = "segredo-supersecreto"
    dados["processes"][0]["command_line_summary"]["observed_flags"] = [
        f"--password={segredo}"
    ]
    with pytest.raises(InventarioInvalido) as capturada:
        analisar(dados)
    assert segredo not in str(capturada.value)


def test_json_malformado_e_rejeitado():
    with pytest.raises(InventarioInvalido):
        analisar_relatorio("{incompleto", agora=AGORA)


def test_campos_desconhecidos_sao_rejeitados():
    dados = relatorio_valido()
    dados["executar_sql"] = True
    with pytest.raises(InventarioInvalido):
        analisar(dados)


def test_evidencia_expirada_e_nao_conforme():
    dados = relatorio_valido()
    dados["expires_at"] = "2026-10-16T14:59:59+00:00"
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_evidencia_administrativa_contraditoria_e_nao_conforme():
    dados = relatorio_valido()
    contraditorio = deepcopy(dados["volumes"][0])
    contraditorio["status"] = "INDETERMINADO"
    dados["volumes"].append(contraditorio)
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_candidato_coerente_nao_comprova_isolamento():
    resultado = analisar(relatorio_valido())
    assert resultado.classificacao is Classificacao.CANDIDATO
    assert resultado.isolamento_comprovado is False


def test_falha_de_fonte_administrativa_torna_resultado_indeterminado():
    dados = relatorio_valido()
    dados["errors"] = ["SHARE_QUERY_FAILED"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


def test_script_tem_padrao_inerte_modos_separados_e_schema_sanitizado():
    fonte = SCRIPT.read_text(encoding="utf-8")
    for marcador in (
        "[switch]$Explain",
        "[switch]$PlanOnly",
        "[switch]$Collect",
        "AuthorizationReference",
        "Escolha exatamente um modo",
        "sensitive_arguments_redacted",
        "ConvertTo-Json",
    ):
        assert marcador in fonte
    assert "Invoke-ReadonlyCollection" in fonte
    assert fonte.count("$report = Invoke-ReadonlyCollection") == 1
    assert fonte.index("$report = Invoke-ReadonlyCollection") > fonte.index(
        "if ([string]::IsNullOrWhiteSpace($AuthorizationReference))"
    )


def test_script_nao_contem_sql_rede_elevacao_ou_mutacoes_administrativas():
    fonte = SCRIPT.read_text(encoding="utf-8").lower()
    proibidos = (
        "invoke-sqlcmd", "invoke-expression", "start-service",
        "stop-service", "restart-service", "set-service", "new-service",
        "set-itemproperty", "new-itemproperty", "remove-itemproperty",
        "new-netfirewallrule", "set-netfirewallrule", "install-module",
        "start-process", "invoke-webrequest", "invoke-restmethod",
        "test-netconnection", "new-object net.sockets", "get-childitem env:",
    )
    assert not any(item in fonte for item in proibidos)


def test_testes_nao_executam_powershell_nem_comandos_administrativos():
    arvore = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    importacoes = {
        alias.name
        for no in ast.walk(arvore)
        if isinstance(no, (ast.Import, ast.ImportFrom))
        for alias in no.names
    }
    assert not {"subprocess", "os", "socket", "pymysql", "requests"} & importacoes


def test_modulo_analisador_nao_importa_runtime_sql_rede_ou_windows():
    fonte = Path("prototypes/oc73_inventario_analisador.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    importacoes = {
        alias.name
        for no in ast.walk(arvore)
        if isinstance(no, (ast.Import, ast.ImportFrom))
        for alias in no.names
    }
    prefixos = ("modules", "operations", "web", "pymysql", "socket", "requests", "win32")
    assert not any(nome.startswith(prefixos) for nome in importacoes)


def test_documentacao_cobre_fontes_privacidade_estados_e_limitacoes():
    texto = DOC_INVENTARIO.read_text(encoding="utf-8")
    for item in (
        "Get-CimInstance",
        "Get-NetTCPConnection",
        "Get-AuthenticodeSignature",
        "Get-FileHash",
        "Get-Volume",
        "Get-SmbShare",
        "OBSERVADO",
        "NÃO OBSERVADO",
        "INDETERMINADO",
        "NÃO APLICÁVEL",
        "CANDIDATO",
        "isolamento_comprovado=false",
        "linha de comando",
        "revisão humana",
    ):
        assert item in texto


def test_autorizacao_permanece_nao_concedida_e_separa_gravacao():
    texto = DOC_AUTORIZACAO.read_text(encoding="utf-8")
    assert "APROVAÇÃO NÃO CONCEDIDA" in texto
    for campo in (
        "Responsável autorizador",
        "Máquina autorizada",
        "Usuário executor",
        "Janela de execução",
        "Hash/revisão do script",
        "Comandos permitidos",
        "Permissão ou proibição de gravar JSON",
        "Diretório de saída",
        "Prazo de validade",
        "Condições de interrupção",
    ):
        assert campo in texto
    assert "A autorização de\ncoleta não implica autorização de gravação" in texto


def test_servico_parado_usa_pid_nulo_sem_invalidar_relatorio():
    dados = relatorio_valido()
    dados["services"].append({
        "status": "OBSERVADO",
        "name": "MySQL-antigo",
        "state": "STOPPED",
        "pid": None,
        "executable": "D:\\mysql-antigo\\bin\\mysqld.exe",
    })
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_servico_iniciado_exige_pid_positivo_para_correlacao():
    dados = relatorio_valido()
    dados["services"][0]["pid"] = None
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize("pid", [0, -1, True])
def test_pid_zero_negativo_ou_booleano_e_rejeitado(pid):
    dados = relatorio_valido()
    dados["services"][0]["pid"] = pid
    with pytest.raises(InventarioInvalido):
        analisar(dados)


@pytest.mark.parametrize("colecao", ["processes", "listeners"])
@pytest.mark.parametrize("pid", [None, 0, -1, False])
def test_processo_e_listener_exigem_pid_positivo(colecao, pid):
    dados = relatorio_valido()
    dados[colecao][0]["pid"] = pid
    with pytest.raises(InventarioInvalido):
        analisar(dados)


def test_servico_sem_pid_nao_e_correlacionado_falsamente_a_porta():
    dados = relatorio_valido()
    dados["services"] = [{
        "status": "OBSERVADO",
        "name": "MySQL57-parado",
        "state": "STOPPED",
        "pid": None,
        "executable": "D:\\mysql57-lab\\bin\\mysqld.exe",
    }]
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_volume_duplicado_identico_e_deduplicado():
    dados = relatorio_valido()
    dados["volumes"].append(deepcopy(dados["volumes"][0]))
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_volume_sem_identificador_ou_sem_associacao_e_indeterminado():
    dados = relatorio_valido()
    dados["processes"][0]["volume_id"] = None
    dados["volumes"] = [{"status": "INDETERMINADO", "id": None}]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


def test_volume_indeterminado_nao_e_promovido_a_dedicado():
    dados = relatorio_valido()
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize(
    ("metadata", "versao", "esperado"),
    [
        ("MYSQL_UNKNOWN_EDITION", "5.7.32", Classificacao.INDETERMINADO),
        ("MYSQL_COMMUNITY", None, Classificacao.INDETERMINADO),
        ("OTHER", "5.7.32", Classificacao.NAO_CONFORME),
        ("MYSQL_COMMUNITY", "5.7.32", Classificacao.CANDIDATO),
    ],
)
def test_identidade_de_produto_e_conservadora(metadata, versao, esperado):
    dados = relatorio_valido()
    processo = dados["processes"][0]
    processo["product_metadata"] = metadata
    processo["version_metadata"] = versao
    assert analisar(dados).classificacao is esperado


@pytest.mark.parametrize("endereco", ["::1", "0.0.0.0", "::", "192.168.1.10"])
def test_somente_loopback_ipv4_literal_e_aceito(endereco):
    dados = relatorio_valido()
    dados["listeners"][0]["local_address"] = endereco
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_listener_duplicado_identico_e_normalizado():
    dados = relatorio_valido()
    dados["listeners"].append(deepcopy(dados["listeners"][0]))
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_nao_observado_nao_prova_ausencia_de_replicacao():
    dados = relatorio_valido()
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize(
    "codigo",
    [
        "OS_QUERY_FAILED",
        "SERVICE_QUERY_FAILED",
        "LISTENER_QUERY_FAILED",
        "PROCESS_QUERY_FAILED",
        "VOLUME_QUERY_FAILED",
        "SHARE_QUERY_FAILED",
        "HASH_FAILED",
        "SIGNATURE_FAILED",
    ],
)
def test_falhas_de_fontes_sao_categoricas_e_indeterminadas(codigo):
    dados = relatorio_valido()
    dados["errors"] = [codigo]
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_processo_trocado_entre_observacoes_e_nao_conforme():
    dados = relatorio_valido()
    dados["processes"][0]["stable_observation"] = False
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_json_com_chave_duplicada_e_rejeitado():
    texto = json.dumps(relatorio_valido(), ensure_ascii=False)
    texto = texto.replace(
        '"schema_version": "oc73-inventario-windows-v2"',
        '"schema_version": "oc73-inventario-windows-v2", "schema_version": "x"',
        1,
    )
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(texto, agora=AGORA)


def test_relatorio_e_listas_excessivos_sao_rejeitados():
    dados = relatorio_valido()
    dados["services"] = [deepcopy(dados["services"][0]) for _ in range(65)]
    with pytest.raises(InventarioInvalido):
        analisar(dados)
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(" " * 262_145, agora=AGORA)


@pytest.mark.parametrize(
    "caminho",
    [
        "\\\\servidor\\share\\mysqld.exe",
        "mysql\\bin\\mysqld.exe",
        "D:\\mysql57\\..\\operacional\\mysqld.exe",
        "D:\\mysql57\\bin\\malicioso\n.exe",
    ],
)
def test_caminhos_remotos_relativos_ou_maliciosos_sao_rejeitados(caminho):
    dados = relatorio_valido()
    dados["processes"][0]["executable"] = caminho
    with pytest.raises(InventarioInvalido):
        analisar(dados)


def test_caminhos_sao_normalizados_sem_resolver_filesystem():
    dados = relatorio_valido()
    dados["processes"][0]["executable"] = "D:/mysql57-lab/bin/mysqld.exe"
    dados["services"][0]["executable"] = "D:/mysql57-lab/bin/mysqld.exe"
    dados["processes"][0]["datadir"] = "D:/mysql57-lab/data"
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_estado_parado_com_pid_positivo_e_inconsistente():
    dados = relatorio_valido()
    dados["services"][0]["state"] = "STOPPED"
    with pytest.raises(InventarioInvalido):
        analisar(dados)


def test_xampp_em_outro_servico_bloqueia_sem_inspecionar_dados():
    dados = relatorio_valido()
    dados["services"].append({
        "status": "OBSERVADO",
        "name": "mysql-xampp",
        "state": "STOPPED",
        "pid": None,
        "executable": "C:\\xampp\\mysql\\bin\\mysqld.exe",
    })
    assert analisar(dados).classificacao is Classificacao.NAO_CONFORME


def test_fluxo_powershell_valida_destino_antes_da_coleta():
    fonte = SCRIPT.read_text(encoding="utf-8")
    assert fonte.index("$validatedOutput = Test-OutputDestination") < fonte.index(
        "$report = Invoke-ReadonlyCollection"
    )
    bloco = fonte.split("function Invoke-ReadonlyCollection", 1)[1]
    assert bloco.index("Get-NetTCPConnection") < bloco.index(
        "-ClassName Win32_Process"
    )
    assert "-Filter ('ProcessId = {0}'" in bloco


def test_gravacao_usa_create_new_revalida_e_fecha_recursos():
    fonte = SCRIPT.read_text(encoding="utf-8")
    for marcador in (
        "FileMode]::CreateNew",
        "FileShare]::None",
        "Test-OutputDestination -LiteralPath $ValidatedDestination.full_path",
        "finally",
        "$writer.Dispose()",
        "$stream.Dispose()",
        "ReparsePoint",
        "Arquivo de saída já existe",
        "Destino mudou após a validação",
    ):
        assert marcador in fonte
    assert "WriteAllText" not in fonte


def test_politicas_de_caminho_de_saida_estao_antes_da_coleta():
    fonte = SCRIPT.read_text(encoding="utf-8")
    validacao = fonte.split("function Test-OutputDestination", 1)[1].split(
        "function Write-ExclusiveJson", 1
    )[0]
    for marcador in (
        "IsPathRooted",
        "LiteralPath -match",
        "GetDirectoryName",
        "PSProvider.Name -ne 'FileSystem'",
        "ReparsePoint",
        "Test-Path -LiteralPath $fullPath",
    ):
        assert marcador in validacao


def test_script_nao_enumera_todos_os_processos_nem_hashes_irrelevantes():
    fonte = SCRIPT.read_text(encoding="utf-8")
    coleta = fonte.split("function Invoke-ReadonlyCollection", 1)[1].split(
        "$modeCount", 1
    )[0]
    assert "Get-CimInstance -ClassName Win32_Process |" not in coleta
    assert coleta.count("-ClassName Win32_Process") == 2
    assert coleta.index("$targetPids") < coleta.index("Get-FileHash")


def test_referencia_de_autorizacao_e_validada_antes_da_coleta():
    fonte = SCRIPT.read_text(encoding="utf-8")
    assert fonte.index(
        "if ([string]::IsNullOrWhiteSpace($AuthorizationReference))"
    ) < fonte.index("$report = Invoke-ReadonlyCollection")


def test_documento_de_hardening_registra_politicas_e_no_go():
    texto = DOC_HARDENING.read_text(encoding="utf-8")
    for item in (
        "Achados, causa e correção",
        "Contrato de PID",
        "Política de sanitização",
        "Política de destino e gravação",
        "Política de produto",
        "Política de endereços e replicação",
        "FileMode.CreateNew",
        "APROVAÇÃO NÃO CONCEDIDA",
        "NO-GO",
    ):
        assert item in texto


def test_produto_administrativo_falsificado_no_json_bruto_e_rejeitado():
    dados = relatorio_valido()
    dados["processes"][0]["administrative_product"] = "MYSQL_COMMUNITY_5_7_32"
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_volume_autodeclarado_dedicado_no_json_bruto_e_rejeitado():
    dados = relatorio_valido()
    dados["volumes"][0]["dedicated"] = True
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_ausencia_de_replicacao_autodeclarada_no_json_bruto_e_rejeitada():
    dados = relatorio_valido()
    dados["replication"]["administrative_absence_confirmed"] = True
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_evidencia_externa_sem_responsavel_e_rejeitada():
    evidencia = evidencia_externa_valida()
    evidencia["responsible"] = " "
    with pytest.raises(InventarioInvalido):
        analisar(relatorio_valido(), evidencia)


def test_evidencia_externa_expirada_e_nao_conforme():
    evidencia = evidencia_externa_valida()
    evidencia["valid_until"] = "2026-10-16T14:59:59+00:00"
    assert analisar(relatorio_valido(), evidencia).classificacao is Classificacao.NAO_CONFORME


def test_evidencia_externa_de_outro_processo_e_nao_conforme():
    evidencia = evidencia_externa_valida()
    evidencia["process"]["pid"] = 4343
    evidencia["process"]["instance_marker"] = "4343|2026-10-16T14:30:00.0000000Z"
    assert analisar(relatorio_valido(), evidencia).classificacao is Classificacao.NAO_CONFORME


def test_evidencia_externa_de_outra_instancia_e_nao_conforme():
    evidencia = evidencia_externa_valida()
    evidencia["instance_id"] = "outra-instancia-127.0.0.1-3308"
    assert analisar(relatorio_valido(), evidencia).classificacao is Classificacao.NAO_CONFORME


@pytest.mark.parametrize(
    "caminho",
    [
        "A:\\mysql\\mysqld.exe",
        "B:\\mysql\\mysqld.exe",
        "D:mysql\\mysqld.exe",
        "\\\\servidor\\share\\mysqld.exe",
        "\\\\?\\D:\\mysql\\mysqld.exe",
        "\\\\.\\D:\\mysql\\mysqld.exe",
        "D:\\mysql\\CON.txt",
        "D:\\mysql\\arquivo. ",
        "D:\\mysql\\arquivo:stream",
    ],
)
def test_caminho_windows_invalido_e_rejeitado(caminho):
    dados = relatorio_valido()
    dados["processes"][0]["executable"] = caminho
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_json_sintetico_no_formato_do_powershell_e_aceito_sem_promocao():
    resultado = analisar_relatorio(
        json.dumps(relatorio_valido(), ensure_ascii=False), agora=AGORA
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_sem_verificador_confiavel_nao_promove_para_candidato():
    resultado = analisar_relatorio(
        relatorio_valido(), agora=AGORA,
        evidencia_externa=evidencia_externa_valida(),
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_verificador_que_recusa_procedencia_mantem_indeterminado():
    resultado = analisar(
        relatorio_valido(), evidencia_externa_valida(), verificador=lambda _: False
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_powershell_nao_produz_afirmacoes_administrativas():
    fonte = SCRIPT.read_text(encoding="utf-8")
    coleta = fonte.split("function Invoke-ReadonlyCollection", 1)[1].split(
        "$modeCount", 1
    )[0]
    assert "administrative_product" not in coleta
    assert "administrative_absence_confirmed" not in coleta
    assert "dedicated =" not in coleta
    assert "oc73-inventario-windows-v2" in coleta


def test_contrato_produtor_consumidor_tem_campos_brutos_exatos():
    dados = relatorio_valido()
    assert set(dados["processes"][0]) == {
        "status", "pid", "name", "executable", "product_metadata",
        "version_metadata", "sha256", "signature_status", "datadir",
        "volume_id", "instance_marker", "stable_observation",
        "command_line_summary",
    }
    assert set(dados["volumes"][0]) == {"status", "id"}
    assert set(dados["replication"]) == {"status", "indicators"}
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_powershell_endurece_unidade_componentes_e_dispositivos():
    fonte = SCRIPT.read_text(encoding="utf-8")
    validacao = fonte.split("function Test-OutputDestination", 1)[1].split(
        "function Write-ExclusiveJson", 1
    )[0]
    for marcador in (
        "^[C-Zc-z]:\\\\", "\\x00-\\x1f\\x7f", "CON|PRN|AUX|NUL",
        "COM[1-9]", "LPT[1-9]", "componente inválido", "malformado",
    ):
        assert marcador in validacao


def test_documento_final_preserva_no_go_e_contrato_externo():
    texto = DOC_FECHAMENTO.read_text(encoding="utf-8")
    for item in (
        "APROVAÇÃO NÃO CONCEDIDA",
        "oc73-administrative-evidence-v2",
        "observação",
        "procedência",
        "FileMode.CreateNew",
        "PRONTO PARA SOLICITAR AUTORIZAÇÃO DE COLETA",
        "NO-GO",
    ):
        assert item in texto


def test_evidencia_v2_vinculada_ao_hash_exato_do_inventario():
    dados = relatorio_valido()
    bruto = serializar_relatorio(dados)
    evidencia = evidencia_externa_valida(bruto)
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia,
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.CANDIDATO


def test_evidencia_nao_pode_ser_reutilizada_com_outro_inventario():
    original = serializar_relatorio(relatorio_valido())
    alterado = relatorio_valido()
    alterado["os"]["version"] = "10.0.1"
    resultado = analisar_relatorio(
        serializar_relatorio(alterado),
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(original),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.NAO_CONFORME


def test_json_semanticamente_igual_com_bytes_diferentes_diverge():
    dados = relatorio_valido()
    compacto = serializar_relatorio(dados)
    identado = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
    assert json.loads(compacto) == json.loads(identado)
    assert hashlib.sha256(compacto).digest() != hashlib.sha256(identado).digest()
    resultado = analisar_relatorio(
        identado,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(compacto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.NAO_CONFORME


def test_alteracao_de_um_unico_campo_invalida_vinculo_criptografico():
    dados = relatorio_valido()
    bruto = serializar_relatorio(dados)
    dados["host_contract"]["user"] = "outro_usuario"
    resultado = analisar_relatorio(
        serializar_relatorio(dados),
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.NAO_CONFORME


def test_hash_de_inventario_invalido_na_evidencia_e_rejeitado():
    evidencia = evidencia_externa_valida()
    evidencia["inventory_sha256"] = "digest-invalido"
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(
            serializar_relatorio(relatorio_valido()),
            agora=AGORA,
            evidencia_externa=evidencia,
        )


def test_revisao_aprovada_ausente_mantem_indeterminado():
    bruto = serializar_relatorio(relatorio_valido())
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_revisao_aprovada_divergente_e_nao_conforme():
    bruto = serializar_relatorio(relatorio_valido())
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="c" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.NAO_CONFORME


def test_revisao_aprovada_correspondente_permite_avaliar_procedencia():
    assert analisar(relatorio_valido()).classificacao is Classificacao.CANDIDATO


def test_callback_true_nao_equivale_a_resultado_de_procedencia():
    bruto = serializar_relatorio(relatorio_valido())
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=lambda _: True,
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_excecao_no_verificador_mantem_indeterminado():
    bruto = serializar_relatorio(relatorio_valido())

    def falhar(_):
        raise RuntimeError("indisponível")

    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=falhar,
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_resultado_sem_autoridade_externa_mantem_indeterminado():
    bruto = serializar_relatorio(relatorio_valido())
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=lambda item: resultado_procedencia_valido(item, ""),
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_resultado_de_procedencia_de_outro_inventario_e_ignorado():
    bruto = serializar_relatorio(relatorio_valido())

    def resultado_divergente(item):
        resultado = resultado_procedencia_valido(item)
        return ResultadoProcedencia(
            comprovada=resultado.comprovada,
            evidence_id=resultado.evidence_id,
            inventory_sha256="d" * 64,
            procedure_revision_sha256=resultado.procedure_revision_sha256,
            authority_reference=resultado.authority_reference,
        )

    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia_externa_valida(bruto),
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_divergente,
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_objeto_python_sem_bytes_originais_nao_tem_vinculo_criptografico():
    dados = relatorio_valido()
    evidencia = evidencia_externa_valida(serializar_relatorio(dados))
    resultado = analisar_relatorio(
        dados,
        agora=AGORA,
        evidencia_externa=evidencia,
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=resultado_procedencia_valido,
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_objeto_python_e_bytes_originais_divergentes_sao_rejeitados():
    dados = relatorio_valido()
    outro = relatorio_valido()
    outro["os"]["version"] = "11.0"
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(
            dados,
            agora=AGORA,
            inventario_bytes_originais=serializar_relatorio(outro),
        )


def test_evidencia_v1_permanece_compativel_mas_inconclusiva():
    bruto = serializar_relatorio(relatorio_valido())
    evidencia = evidencia_externa_valida(bruto)
    evidencia["schema_version"] = "oc73-administrative-evidence-v1"
    del evidencia["inventory_sha256"]
    resultado = analisar_relatorio(
        bruto,
        agora=AGORA,
        evidencia_externa=evidencia,
        revisao_procedimento_aprovada="b" * 64,
        verificador_procedencia=lambda _: pytest.fail(
            "verificador não deve ser chamado sem vínculo criptográfico"
        ),
    )
    assert resultado.classificacao is Classificacao.INDETERMINADO


def test_marcador_utc_canonico_e_aceito_integralmente():
    dados = relatorio_valido()
    dados["processes"][0]["instance_marker"] = "4242|2026-10-19T12:30:45.1234567Z"
    bruto = serializar_relatorio(dados)
    evidencia = evidencia_externa_valida(bruto)
    evidencia["process"]["instance_marker"] = dados["processes"][0]["instance_marker"]
    assert analisar(dados, evidencia).classificacao is Classificacao.CANDIDATO


def test_contrato_powershell_normaliza_datetime_datetimeoffset_e_cim():
    fonte = SCRIPT.read_text(encoding="utf-8")
    bloco = fonte.split("function ConvertTo-UtcCreationTimestamp", 1)[1].split(
        "function Get-Plan", 1
    )[0]
    for marcador in (
        "-is [DateTimeOffset]",
        "-is [DateTime]",
        "(?<microsecond>\\d{6})",
        "[Globalization.CultureInfo]::InvariantCulture",
        '"yyyy-MM-dd\'T\'HH:mm:ss.fffffff\'Z\'"',
        ".ToUniversalTime()",
    ):
        assert marcador in bloco
    assert "UtcNow" not in bloco


def test_saida_normalizada_de_representacao_cim_e_aceita_pelo_python():
    dados = relatorio_valido()
    # Equivalente UTC de 20261019123045.123456+180.
    dados["processes"][0]["instance_marker"] = "4242|2026-10-19T09:30:45.1234560Z"
    bruto = serializar_relatorio(dados)
    evidencia = evidencia_externa_valida(bruto)
    evidencia["process"]["instance_marker"] = dados["processes"][0]["instance_marker"]
    assert analisar(dados, evidencia).classificacao is Classificacao.CANDIDATO


@pytest.mark.parametrize(
    "marcador",
    [
        "4242|20261019123045.123456+000",
        "4242|2026-10-19T12:30:45Z",
        "4242|2026-13-19T12:30:45.1234567Z",
        "4343|2026-10-19T12:30:45.1234567Z",
        "invalido",
    ],
)
def test_marcador_invalido_ou_pid_divergente_e_rejeitado(marcador):
    dados = relatorio_valido()
    dados["processes"][0]["instance_marker"] = marcador
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_falha_de_normalizacao_do_marcador_produz_inconclusivo():
    dados = relatorio_valido()
    dados["processes"][0]["instance_marker"] = None
    dados["processes"][0]["stable_observation"] = False
    dados["errors"] = ["PROCESS_OBSERVATION_INCOMPLETE"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize("alteracao", ["pid", "creation_date", "executable"])
def test_mudanca_entre_observacoes_permanece_nao_conforme(alteracao):
    dados = relatorio_valido()
    dados["processes"][0]["stable_observation"] = False
    dados["errors"] = ["PROCESS_CHANGE_CONFIRMED"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.NAO_CONFORME


@pytest.mark.parametrize(
    "falha",
    [
        "segunda_consulta_falhou",
        "segunda_consulta_zero",
        "segunda_consulta_multipla",
        "timestamp_inicial_invalido",
        "timestamp_final_invalido",
        "executavel_inicial_ausente",
        "executavel_final_ausente",
    ],
)
def test_observacao_incompleta_permanece_indeterminada_e_nunca_candidata(falha):
    dados = relatorio_valido()
    dados["processes"][0]["stable_observation"] = False
    dados["errors"] = ["PROCESS_OBSERVATION_INCOMPLETE"]
    if falha == "timestamp_inicial_invalido":
        dados["processes"][0]["instance_marker"] = None
    if falha == "executavel_inicial_ausente":
        dados["processes"][0]["executable"] = None
        dados["services"][0]["executable"] = None
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


def test_codigo_legado_process_changed_e_tratado_como_evidencia_insuficiente():
    dados = relatorio_valido()
    dados["processes"][0]["stable_observation"] = False
    dados["errors"] = ["PROCESS_CHANGED"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


def test_observacao_estavel_identica_permanece_candidata():
    dados = relatorio_valido()
    assert analisar(dados).classificacao is Classificacao.CANDIDATO


def test_codigo_de_mudanca_confirmada_prevalece_sobre_flag_estavel_contraditoria():
    dados = relatorio_valido()
    dados["errors"] = ["PROCESS_CHANGE_CONFIRMED"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.NAO_CONFORME


@pytest.mark.parametrize("campo", ["executable", "datadir"])
def test_processo_sem_campo_opcional_permanece_indeterminado(campo):
    dados = relatorio_valido()
    dados["processes"][0][campo] = None
    if campo == "executable":
        dados["services"][0]["executable"] = None
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize(
    "codigo",
    [
        "SERVICE_QUERY_FAILED",
        "VOLUME_QUERY_FAILED",
        "SIGNATURE_FAILED",
        "HASH_FAILED",
    ],
)
def test_falha_parcial_compativel_nunca_promove_candidato(codigo):
    dados = relatorio_valido()
    dados["errors"] = [codigo]
    assert analisar(dados).classificacao is Classificacao.INDETERMINADO


def test_falha_da_consulta_de_processo_e_inconclusiva_nao_divergente():
    dados = relatorio_valido()
    dados["processes"] = []
    dados["errors"] = ["PROCESS_QUERY_FAILED"]
    assert analisar_relatorio(dados, agora=AGORA).classificacao is Classificacao.INDETERMINADO


@pytest.mark.parametrize("status", [[], {}])
def test_status_com_tipo_estrutural_invalido_e_rejeitado(status):
    dados = relatorio_valido()
    dados["os"]["status"] = status
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


@pytest.mark.parametrize(("campo", "valor"), [("local_port", True), ("pid", False)])
def test_booleano_nao_e_aceito_como_inteiro_de_listener(campo, valor):
    dados = relatorio_valido()
    dados["listeners"][0][campo] = valor
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(dados, agora=AGORA)


def test_bytes_utf8_invalidos_sao_rejeitados_uniformemente():
    with pytest.raises(InventarioInvalido):
        analisar_relatorio(b'{"schema_version":"\xff"}', agora=AGORA)


def test_matriz_estatica_produtor_consumidor_cobre_falhas_parciais():
    fonte = SCRIPT.read_text(encoding="utf-8")
    for codigo in (
        "OS_QUERY_FAILED",
        "SERVICE_QUERY_FAILED",
        "LISTENER_QUERY_FAILED",
        "PROCESS_QUERY_FAILED",
        "VOLUME_QUERY_FAILED",
        "SHARE_QUERY_FAILED",
        "HASH_FAILED",
        "SIGNATURE_FAILED",
        "PROCESS_CHANGE_CONFIRMED",
        "PROCESS_OBSERVATION_INCOMPLETE",
    ):
        assert codigo in fonte
    analisador = Path("prototypes/oc73_inventario_analisador.py").read_text(encoding="utf-8")
    assert '"PROCESS_CHANGED"' in analisador
    assert "executable = if ($path) { $path } else { $null }" in fonte
    assert "instance_marker = $instanceMarker" in fonte
    assert "New-Evidence 'INDETERMINADO' @{ path = $null }" in fonte
    assert "$afterPid -ne [int64]$process.ProcessId" in fonte
    assert "$afterCreationTimestamp -cne $creationTimestamp" in fonte
    assert "$afterPath -cne $path" in fonte
    assert "if ($after.Count -ne 1)" in fonte
    assert "[void]$errors.Add('PROCESS_CHANGE_CONFIRMED')" in fonte
    assert "[void]$errors.Add('PROCESS_OBSERVATION_INCOMPLETE')" in fonte
    assert "[void]$errors.Add('PROCESS_CHANGED')" not in fonte
