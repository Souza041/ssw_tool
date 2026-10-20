# OC73 Sprint 3.6 — auditoria estática e plano de consulta

**Parecer: BLOQUEADO para a consulta operacional completa nesta etapa.** A auditoria de schema informada pelo responsável técnico atesta o contrato mínimo, mas a integridade operacional ainda não foi consultada. Este documento resulta apenas de leitura do código e de testes com fakes. Nenhuma conexão, automação ou migration foi executada.

## Inventário exato de SQL do modo completo

A fonte normativa dos textos SQL é `SQL_FIXO` em `scripts/diagnostico_oc73_operacional_readonly.py`. A ordem efetiva é:

| Ordem | Identificador | Classe e resultado | Acesso a linhas operacionais |
| --- | --- | --- | --- |
| 1 | `iniciar_transacao_read_only` | `START TRANSACTION READ ONLY`; não retorna linhas | Não |
| 2 | `identidade` | `SELECT` de versão, distribuição, UUID, hostname, porta e `DATABASE()` | Não |
| 3 | `tabelas` | `SELECT` de `information_schema.TABLES` | Não |
| 4 | `colunas` | `SELECT` de `information_schema.COLUMNS` | Não |
| 5 | `indices` | `SELECT` de `information_schema.STATISTICS`, inclusive `SUB_PART` | Não |
| 6 | `constraints` | `SELECT` de `TABLE_CONSTRAINTS` e `KEY_COLUMN_USAGE` | Não |
| 7 | `foreign_keys` | `SELECT` de `REFERENTIAL_CONSTRAINTS` | Não |
| 8–9 | `ddl_execucoes`, `ddl_tentativas` | `SHOW CREATE TABLE` das duas tabelas | Não |
| 10 | `duplicidades_scheduler` | `SELECT` agregado por `data_referencia, triggered_by`, limitado a 101 grupos anormais | Sim; não retorna chaves |
| 11 | `duplicidades_ctrc` | `SELECT` agregado por `serie, numero, codigo_ocorrencia`, limitado a 101 grupos anormais | Sim; não retorna CTRCs |
| 12 | `duplicidades_referencia` | `SELECT` agregado por referência, limitado a 101 grupos anormais | Sim; não retorna referências |
| 13 | `tentativas_orfas` | `SELECT` agregado sobre `LEFT JOIN`, limitado a 101 IDs dentro da subconsulta | Sim; não retorna IDs |
| 14 | `integridade_estados` | `SELECT` com duas contagens de status fora do domínio conhecido | Sim; não retorna registros |

Portanto, **nem toda instrução é SELECT**: há `START TRANSACTION READ ONLY` e dois `SHOW CREATE TABLE`. Todas as instruções são consultivas; não há `INSERT`, `UPDATE`, `DELETE`, `CALL`, `GET_LOCK`, `SELECT ... FOR UPDATE`, exportação ou SQL fornecido pelo operador no inventário diagnóstico. O `GROUP BY` usa chaves operacionais internamente, mas só devolve contagens agregadas. `TABLE_ROWS` é estimativa de metadados, não contagem exata.

## Achados

| ID | Criticidade | Evidência | Consequência e recomendação |
| --- | --- | --- | --- |
| A1 | Alta | `SQL_FIXO` linhas 135–187; `_consultar_integridade()` linhas 771–865 | As cinco consultas podem percorrer grande parte das tabelas. `LIMIT 101` limita grupos retornados, não garante pouca leitura. `MAX_EXECUTION_TIME(5000)` é solicitado por instrução e não é orçamento total rígido. Em hospedagem compartilhada, obter estimativa de volume/plano com DBA e aprovar janela antes de executar. Considerar separar ou omitir consultas redundantes com UNIQUE confirmado, após revisão. |
| A2 | Alta | `main()` linhas 1045–1066 | No modo completo, falha de uma agregação é marcada `inconclusivo`, mas o CLI ainda pode terminar com código 0. Corrigir o código de saída para integridade inconclusiva/limitada antes de usar esse CLI como gate; conferir o JSON manualmente enquanto isso. |
| A3 | Média | `diagnosticar()` linhas 965–1001 | O JSON completo inclui host configurado, hostname, UUID e DDL. Não imprimi-lo em console, CI ou logs amplos. Propor saída resumida que exponha apenas conformidade e agregados; o comando futuro abaixo mantém o JSON integral somente em memória e imprime projeção agregada. |
| A4 | Média | `SQL_FIXO` linhas 147–167; schema informado pelo responsável | As duas consultas de duplicidade por CTRC e referência são logicamente redundantes se os respectivos UNIQUEs conformes estiverem ativos. Ainda podem impor custo de varredura. Qualquer omissão deve ser justificada e testada antes de alterar o script. |
| A5 | Média | `integridade_estados` linhas 178–187 | Conta status inválidos, mas não quantifica tentativas antigas em `reservado`, `envio_iniciado` ou `incerto`. Não concluir que são seguras para reenvio. Planejar agregados de idade/status sem IDs, com custo e privacidade revisados separadamente. |
| A6 | Média | `repository.py` linhas 119–208 | `GET_LOCK` serializa produtores cooperantes por fluxo/data, mas não há UNIQUE por fluxo/data. `NULL`/resposta ausente é tratada como trava ocupada: bloqueia, porém perde diagnóstico. Observar falha de `RELEASE_LOCK` e encerramento da sessão; não assumir que um processo externo respeita o protocolo. |
| A7 | Baixa | `diagnosticar()` linhas 1009–1018 | `rollback()` e `close()` são chamados no `finally`, mas falhas são suprimidas. O processo não comprova liberação imediata de sessão após erro de fechamento. Documentar como incerteza operacional e acompanhar sessões com o DBA se ocorrer. |

O script valida schema, porta, versão e UUID antes de consultar as tabelas (`validar_identidade()`, linhas 294–324). A configuração exige `AUTOMATION_DB_*` completos e schema esperado antes da conexão (linhas 204–234). Erros estruturais/conexão viram `DiagnosticoSeguroErro` sem detalhes do driver; erros dos agregados tornam o restante inconclusivo, sem vazar a exceção no JSON. `connect_timeout=5`, `read_timeout=15` e `write_timeout=5` são por operação, não um limite global. Conta com privilégios somente leitura deve ser confirmada pelo DBA: `--somente-leitura` e `START TRANSACTION READ ONLY` não verificam os grants da conta.

Consultas longas podem consumir CPU, I/O ou tabelas temporárias. `SHOW CREATE TABLE` e leituras de metadados podem adquirir metadata locks; uma transação aberta até o rollback pode reter esses locks durante as agregações. Não há `SELECT ... FOR UPDATE` no diagnóstico. Não foi executado `EXPLAIN`, não há tamanho exato das tabelas nem medição de carga nesta etapa; o custo real permanece incerto.

## GET_LOCK e concorrência

`reservar_execucao_agendada()` usa `ssw_tool:oc73:{CWB|BIG}:{data ISO}` e `SELECT GET_LOCK(nome, 0)` na **mesma conexão** usada para consultar e inserir `oc73_execucoes`. O timeout de aquisição é zero: se a trava não estiver livre, não espera; o round trip ainda depende do timeout de leitura. CWB e BIG têm nomes diferentes. A conexão mantém o lock até `RELEASE_LOCK` no `finally` ou encerramento da sessão; `commit` da reserva ocorre antes da liberação. Se já existe execução para fluxo/data, a função faz rollback e retorna bloqueio. Perda de conexão/commit gera exceção e impede o service de prosseguir. Um valor `NULL` em `GET_LOCK` também bloqueia, mas é rotulado `trava_ocupada`.

Essa trava protege **reserva de execução agendada entre processos cooperantes**, não o CTRC. A prevenção de dois envios para o mesmo `(serie, numero, 73)` depende de `uq_oc73_ctrc` e da transição condicional para `envio_iniciado`. A inexistência de UNIQUE por fluxo/data permanece um risco para produtores que não usem `GET_LOCK`.

## Estados e falhas

| Estado persistido | Entrada/saída permitida no código | Falha ou recuperação conservadora |
| --- | --- | --- |
| `reservado` | `INSERT` confirmado; apenas `UPDATE` condicional da mesma execução pode iniciar envio | Falha antes da transição deixa a reserva bloqueada. O reconciliador não a reenvia nem a confirma automaticamente. |
| `envio_iniciado` | Gravado antes do POST; pode virar `confirmado` após achar código 73 e referência exata, ou `incerto` após exceção | Crash pode ocorrer antes ou depois do POST. O estado sozinho não prova envio. Reexecução encontra reserva existente e não reenvia. |
| `incerto` | Pode virar `confirmado` por referência na reconciliação | Timeout/resposta ambígua/erro de confirmação não autorizam retry do POST. Se histórico não mostra a referência, permanece bloqueado. |
| `confirmado` | Terminal para envio; reconciliação reconhece o estado | Falha de commit de confirmação pode deixar o resultado percebido diferente do persistido; verificar antes de qualquer ação manual. |

Execução agendada `executando` antiga também bloqueia novo disparo para o mesmo fluxo/data, qualquer que seja a causa da interrupção. Não há reenvio automático de tentativas antigas sem referência.

## Testes offline e lacunas

Os testes com fakes cobrem o inventário SQL, transação consultiva, identidade errada, sanitização de exceções, resultado limitado/inconclusivo, falha por permissão/timeout nos metadados, parada após falha no primeiro agregado, `GET_LOCK` ocupado/`NULL`, release e fechamento, transições e reconciliação. A suíte isolada executada nesta sprint está registrada na entrega. Testes MySQL reais existentes foram deliberadamente excluídos.

Lacunas: custo/plano real no servidor compartilhado; privilégio efetivo da futura conta somente leitura; contagem de estados antigos; confirmação de liberação imediata da sessão quando `close()` falha; resposta real do servidor a cada hint de timeout. Nenhuma dessas lacunas foi preenchida com dados inventados.

## Plano para futura consulta, **não executar nesta sprint**

Pré-condições: autorização explícita nova, janela aprovada pelo DBA, conta dedicada somente leitura, estimativa de custo das cinco agregações, revisão dos achados A1–A3 e confirmação independente do UUID. Os quatro gates operacionais no `.env` devem permanecer nos valores de bloqueio; este procedimento apenas os lê.

Modelo de comando PowerShell, **bloqueado até cumprir as pré-condições**. As variáveis `OC73_RO_DB_USER`, `OC73_RO_DB_PASSWORD` e `OC73_SERVER_UUID_APROVADO` devem vir de fonte de segredos aprovada para o processo, sem valores literais no comando. O `.env` fornece somente host, porta e schema; não fornece a conta de diagnóstico. A saída integral do script fica em memória e somente os agregados são apresentados:

```powershell
@'
import io, json, os, runpy, sys
from contextlib import redirect_stdout
from dotenv import dotenv_values

config = dotenv_values('.env')
gates = {
    'OC73_ENVIO_HABILITADO': 'NAO',
    'OCORRENCIA_73_DRY_RUN': 'true',
    'OCORRENCIA_73_ENABLED': 'false',
    'OCORRENCIA_73_MAX_LANCAMENTOS': '0',
}
if any(str(config.get(k, '')).strip() != v for k, v in gates.items()):
    raise SystemExit('Gates OC73 não conformes; diagnóstico cancelado.')
for origem in ('OC73_RO_DB_USER', 'OC73_RO_DB_PASSWORD', 'OC73_SERVER_UUID_APROVADO'):
    if not os.environ.get(origem):
        raise SystemExit('Pré-requisito seguro ausente; diagnóstico cancelado.')
for chave in ('AUTOMATION_DB_HOST', 'AUTOMATION_DB_PORT', 'AUTOMATION_DB_NAME'):
    if not config.get(chave):
        raise SystemExit('Configuração de conexão incompleta; diagnóstico cancelado.')
    os.environ[chave] = str(config[chave])
os.environ['AUTOMATION_DB_USER'] = os.environ['OC73_RO_DB_USER']
os.environ['AUTOMATION_DB_PASSWORD'] = os.environ['OC73_RO_DB_PASSWORD']
sys.argv = [
    'diagnostico_oc73_operacional_readonly', '--somente-leitura',
    '--server-uuid-esperado', os.environ['OC73_SERVER_UUID_APROVADO'],
]
captura = io.StringIO()
with redirect_stdout(captura):
    try:
        runpy.run_module('scripts.diagnostico_oc73_operacional_readonly', run_name='__main__')
    except SystemExit as saida:
        if saida.code not in (0, None):
            raise
resultado = json.loads(captura.getvalue())
resumo = {
    'contrato_minimo': resultado['conformidade_schema']['contrato_minimo'],
    'estado_coleta_integridade': resultado['estado_coleta_integridade'],
    'integridade': resultado['integridade'],
}
print(json.dumps(resumo, ensure_ascii=False, indent=2))
if resumo['contrato_minimo'] != 'conforme' or resumo['estado_coleta_integridade'] != 'completo':
    raise SystemExit(2)
'@ | python -
```

Esse comando não mede a carga previamente e não comprova os grants da conta; ambos são pré-condições externas. Interromper diante de identidade divergente, erro de permissão, timeout, integridade inconclusiva/limitada ou qualquer agregado anormal. Não realizar correção automática, retry, limpeza ou reenvio. Um resultado completo com contagens zero tampouco valida normalização de identidade ou comportamento do SSW.
