# OC73 Sprint 3.6.1 — blindagem do diagnóstico consultivo

**Parecer para a consulta operacional completa: BLOQUEADO.** Esta revisão é exclusivamente offline. O código de saída e a saída resumida foram corrigidos e exercitados com fakes; custo e plano das agregações no MySQL compartilhado seguem sem medição e exigem avaliação do DBA e autorização específica.

## Correções verificadas

| Achado da Sprint 3.6 | Evidência atual | Resultado |
| --- | --- | --- |
| A2 — falso código 0 | `scripts/diagnostico_oc73_operacional_readonly.py`, `resumir_resultado()` e `main()` | O modo completo exige schema conforme e cinco verificações operacionais completas e conformes. Qualquer não conformidade ou inconclusividade retorna 2. Falha de argumentos, configuração, conexão ou execução controlada retorna 3. `--help` retorna 0 sem realizar diagnóstico. |
| A3 — identidade e DDL na saída padrão | Mesmo arquivo, `--resumo`/`--completo` | Resumo é padrão e contém apenas escopo, estado controlado de cada verificação, limites estáticos e motivos de bloqueio controlados. O relatório integral requer `--completo` explícito. |
| Contagem agregada negativa | Mesmo arquivo, `_resultado_integridade()` | Contagem negativa vira coleta inconclusiva, nunca aprovação. |

O resumo não copia metadados de identidade, hostname, UUID, DDL, chaves operacionais nem texto arbitrário de respostas/erros do servidor. Estados inconclusivos permanecem visíveis por verificação. `--apenas-schema` mantém o inventário e a avaliação estrutural existentes, sem executar as cinco agregações. Os SQLs não foram alterados.

## Matriz de custo das cinco agregações

Os índices únicos e a FK citados são **evidência da auditoria apenas-schema informada na Sprint 3.5**. O uso efetivo dos índices, cardinalidade, custo e carga são **estimativas estáticas**, pois nenhum `EXPLAIN` ou agregado foi executado no servidor operacional.

| Consulta fixa | Tabelas, filtro e índice potencial | Trabalho e risco estimados | Alternativa para avaliação futura, sem retirar a verificação |
| --- | --- | --- | --- |
| `duplicidades_scheduler` | `oc73_execucoes`; `triggered_by IN ('scheduler:CWB','scheduler:BIG')`; agrupamento por data e origem. Não foi comprovado índice para esses campos nem UNIQUE fluxo/data. | Pode varrer execuções e usar tabela temporária/ordenação. Sem grupos duplicados, precisa examinar todo o conjunto pertinente. Risco **alto/incerto**. | DBA avaliar plano/cardinalidade e janela de baixa carga; considerar índice específico apenas após decisão separada de schema. |
| `duplicidades_ctrc` | `oc73_tentativas`; sem `WHERE`; agrupamento por `(serie, numero, codigo_ocorrencia)`. `uq_oc73_ctrc` é índice potencial. | Mesmo com índice único, a agregação pode percorrer todo o índice. `LIMIT` não garante leitura curta quando não há duplicatas. Risco **moderado a alto/incerto**. | Verificar plano e custo; avaliar prova formal pelo UNIQUE ativo como substituição somente em mudança posterior aprovada. |
| `duplicidades_referencia` | `oc73_tentativas`; `referencia IS NOT NULL AND referencia <> ''`; agrupamento por referência. `uq_oc73_referencia` é índice potencial. | Pode varrer ampla parte do índice e criar agrupamento. Custo depende de cardinalidade e plano não medidos. Risco **moderado a alto/incerto**. | Avaliar plano e cardinalidade; eventual prova por UNIQUE exige revisão separada, sem simplesmente omitir o controle. |
| `tentativas_orfas` | `oc73_tentativas` `LEFT JOIN` `oc73_execucoes` por FK/PK, filtro `e.id IS NULL`. | Mesmo com índice em `execucao_id` e PK em `id`, ausência de órfãs pode requerer examinar todas as tentativas. Risco **alto/incerto**. | DBA avaliar plano; FK conforme é evidência estrutural, mas não substitui automaticamente a checagem de dados. |
| `integridade_estados` | Duas contagens, uma por tabela; `status IS NULL OR BINARY status NOT IN (...)`. Nenhum índice de status foi comprovado nesta auditoria. | As duas contagens não têm `LIMIT`; `BINARY`/predicado negativo pode impedir uso seletivo de índice. Risco **alto/incerto**. | DBA estimar linhas e plano; considerar agregação por domínio de status em janela aprovada, sem perder a detecção de valores inválidos. |

`LIMIT 101` limita grupos/IDs produzidos pela subconsulta, não linhas examinadas antes de `GROUP BY`, `HAVING` ou `JOIN`. `MAX_EXECUTION_TIME(5000)` é um pedido por SELECT ao servidor MySQL 5.7, não um limite total rígido: não cobre conexão, metadados, espera de locks, rede, rollback nem as outras consultas. O cliente usa `read_timeout=15` por leitura, também sem orçamento global. A transação `READ ONLY` pode manter metadata locks até o rollback. A coluna `TABLE_ROWS` do InnoDB é apenas estimativa, não medição real de volume ou custo.

## Testes offline

`python -m pytest -q tests/test_diagnostico_oc73_operacional_readonly.py`: **60 passed**. Cobertos: sucesso, duplicidade, estado inválido, limite de 101, timeout, permissão negada, resultado parcial, contagem negativa, coleta global inconclusiva, schema conforme/inconclusivo, ausência de agregados em `--apenas-schema`, resumo sem identidade/DDL e relatório integral explícito. Todas as conexões foram substituídas por fakes.

## Consulta futura proposta — não executar nesta sprint

Pré-condições: autorização específica, estimativa de volume/plano e janela aprovadas pelo DBA, conta dedicada somente leitura, UUID esperado confirmado independentemente, e quatro gates OC73 bloqueados no `.env`. Não contornar falhas de identidade, versão, schema, permissão ou consulta. O trecho abaixo apenas ilustra a invocação futura com resumo padrão; as três variáveis `OC73_RO_DB_USER`, `OC73_RO_DB_PASSWORD` e `OC73_SERVER_UUID_APROVADO` devem vir de fonte de segredos aprovada, sem impressão de seus valores.

```powershell
@'
import os, runpy, sys
from dotenv import dotenv_values

env = dotenv_values('.env')
gates = {
    'OC73_ENVIO_HABILITADO': 'NAO',
    'OCORRENCIA_73_DRY_RUN': 'true',
    'OCORRENCIA_73_ENABLED': 'false',
    'OCORRENCIA_73_MAX_LANCAMENTOS': '0',
}
if any(str(env.get(k, '')).strip() != v for k, v in gates.items()):
    raise SystemExit('Gates OC73 não conformes; diagnóstico cancelado.')
if any(not os.environ.get(k) for k in (
    'OC73_RO_DB_USER', 'OC73_RO_DB_PASSWORD', 'OC73_SERVER_UUID_APROVADO'
)):
    raise SystemExit('Pré-requisito seguro ausente; diagnóstico cancelado.')
for key in ('AUTOMATION_DB_HOST', 'AUTOMATION_DB_PORT', 'AUTOMATION_DB_NAME'):
    if not env.get(key):
        raise SystemExit('Configuração de conexão incompleta; diagnóstico cancelado.')
    os.environ[key] = str(env[key])
os.environ['AUTOMATION_DB_USER'] = os.environ['OC73_RO_DB_USER']
os.environ['AUTOMATION_DB_PASSWORD'] = os.environ['OC73_RO_DB_PASSWORD']
sys.argv = [
    'diagnostico_oc73_operacional_readonly', '--somente-leitura', '--resumo',
    '--server-uuid-esperado', os.environ['OC73_SERVER_UUID_APROVADO'],
]
runpy.run_module('scripts.diagnostico_oc73_operacional_readonly', run_name='__main__')
'@ | python -
```

Interromper se houver erro, código 2 ou 3, consulta lenta, limite atingido ou estado inconclusivo. Mesmo código 0 não valida normalização de CTRC, segurança de reenvio de estados antigos nem comportamento do SSW. Não executar retry, correção automática ou lançamento.
