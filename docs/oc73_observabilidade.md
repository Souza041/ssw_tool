# Observabilidade OC73

O painel `/oc73/observabilidade` é exclusivamente de leitura. Ele não oferece
ações para ativar o scheduler, executar o serviço, reconciliar tentativas ou
alterar estados.

## Acesso

O projeto não possui RBAC central para os usuários da sessão SSW. Como contenção,
o painel exige simultaneamente:

- sessão SSW válida; e
- usuário SSW presente na allowlist `OC73_OBSERVABILIDADE_USUARIOS`.

A allowlist é vazia por padrão, portanto o acesso nasce negado. Os nomes são
separados por vírgula. A rota revalida a allowlist a cada requisição; ocultar o
link no menu não é o controle de autorização.

## Dados e limites

- histórico paginado em 25 itens por padrão e no máximo 100;
- filtros parametrizados por fluxo, status e período;
- tentativas consultadas somente para uma execução selecionada;
- alertas de `executando` antigo limitados a 50 itens;
- nenhuma senha, cookie, token ou resposta integral do SSW é exibida.

O limite de alerta é configurável por `OC73_ALERTA_EXECUTANDO_MINUTOS`, com padrão
de 120 minutos. O alerta é informativo e nunca altera a execução.

Quando o scheduler está desligado, o horário futuro é rotulado como previsão
teórica e não como job ativo.

O estado “ativo” é o estado do APScheduler no processo web que respondeu à
requisição. Em implantação com múltiplos processos, ele não representa sozinho
um inventário distribuído de todos os schedulers; as reservas MySQL continuam
sendo a fonte de proteção contra duplicidade.

## Diagnóstico MySQL

O comando abaixo usa apenas consultas hard-coded `SELECT` e `SHOW`:

```powershell
python scripts/diagnostico_oc73_mysql.py
```

Ele não reutiliza a credencial da aplicação. Configure uma conta dedicada e
somente leitura nas variáveis `OC73_DIAGNOSTICO_DB_HOST`, `_PORT`, `_NAME`,
`_USER` e `_PASSWORD`. O relatório inclui `SHOW GRANTS FOR CURRENT_USER` para
revisão dos privilégios efetivos, sem imprimir a senha.

Ele informa versão, identidade efetiva, estrutura, índices e possíveis
duplicidades de reserva agendada. O teste de `GET_LOCK` é opcional, aceito
somente após validar o laboratório `127.0.0.1:3308/oc73_concorrencia_test`, e
exige duas opções explícitas:

```powershell
python scripts/diagnostico_oc73_mysql.py --testar-advisory-lock --confirmar-ambiente-controlado
```

Nenhuma dessas opções executa DML, DDL, migration ou reserva OC73.
