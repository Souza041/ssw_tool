# Nexus Local Agent

## Objetivo e limites

O Local Agent separa o Nexus em dois processos:

- **Servidor VPS:** mantém uma fila SQLite durável e expõe o protocolo HTTPS.
- **Executor Windows:** consulta a fila, renova um lease e executa somente handlers Python registrados.

Não há execução remota de shell, instalação automática de serviço, criação de tarefa no Agendador do Windows ou ativação de flag. O único handler incluído é `system.ping`, sem integração com SSW, Portal GCE, e-mail ou banco operacional.

## Contrato de segurança

- `NEXUS_LOCAL_AGENT_ENABLED=false` por padrão.
- Token exclusivo por agente, enviado como Bearer e comparado em tempo constante.
- HTTPS obrigatório no executor. HTTP exige a opção explícita de homologação `NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP=true`.
- A VPS cruza as capacidades anunciadas pelo executor com `NEXUS_LOCAL_AGENT_ALLOWED_TASKS`.
- Cada identidade também possui sua própria allowlist em `NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON`.
- O executor não recebe nem interpreta comandos arbitrários.
- Enfileiramento e cancelamento administrativo exigem sessão SSW ADMIN e token CSRF.
- Payload, resultado e logs têm limites de tamanho.
- O token de lease nunca é retornado pelas consultas administrativas.

## Configuração da VPS

Gerar um token fora do repositório:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Configurar no ambiente do processo web, sem versionar o segredo:

```text
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_LOCAL_AGENT_TOKENS_JSON={"windows-cwb-01":"TOKEN_GERADO"}
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-cwb-01":["system.ping"]}
NEXUS_LOCAL_AGENT_DB=/var/lib/nexus/local-agent.sqlite3
```

O diretório do SQLite deve ser privado e gravável pelo usuário do serviço web. A aplicação cria somente as tabelas `local_agent_*` nesse arquivo separado.

## Configuração do executor Windows

Use um arquivo de ambiente próprio do executor, com ACL restrita, e não copie o `.env` da VPS:

```text
NEXUS_LOCAL_AGENT_SERVER_URL=https://nexus.exemplo/api-base-se-houver
NEXUS_LOCAL_AGENT_ID=windows-cwb-01
NEXUS_LOCAL_AGENT_TOKEN=TOKEN_GERADO
NEXUS_LOCAL_AGENT_POLL_SECONDS=5
NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS=15
NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP=false
```

Homologação de um único polling, em terminal foreground:

```powershell
python scripts\nexus_local_agent_windows.py --once
```

Execução contínua, ainda em foreground:

```powershell
python scripts\nexus_local_agent_windows.py
```

Esta entrega não instala serviço Windows. Após homologação, um serviço deve ser criado por procedimento operacional separado, usando conta de privilégio mínimo, diretório de trabalho fixo e recuperação com backoff.

## API administrativa

Com uma sessão ADMIN autenticada:

1. `GET /admin/local-agent` retorna agentes, jobs, allowlist e um `csrf_token`.
2. `POST /admin/local-agent/jobs`, com `X-CSRF-Token`, enfileira uma tarefa.
3. `GET /admin/local-agent/jobs/{id}` retorna estado, progresso e logs.
4. `POST /admin/local-agent/jobs/{id}/cancel`, com CSRF, cancela ou solicita cancelamento.

Exemplo de corpo para homologação:

```json
{
  "task": "system.ping",
  "payload": {"message": "homologacao"},
  "priority": 100,
  "max_attempts": 2
}
```

## Lease, concorrência e recuperação

O claim usa `BEGIN IMMEDIATE`, portanto somente um agente recebe cada job. Cada claim gera um token aleatório de lease. Heartbeat, logs e conclusão exigem o mesmo agente e token. Um lease expirado volta à fila enquanto houver tentativas; ao esgotá-las, o job termina em `FAILED`.

Cancelamento de job em fila é imediato. Em execução, é cooperativo: o heartbeat sinaliza o cancelamento ao handler. Novos handlers devem consultar `context.raise_if_cancelled()` entre etapas seguras.

## Como adicionar um handler produtivo futuramente

1. Implementar uma função `(payload, context) -> dict` em módulo específico.
2. Validar todos os campos do payload antes de tocar a integração.
3. Registrar explicitamente o handler no executor.
4. Adicionar o nome exato à allowlist da VPS.
5. Criar testes offline com clientes e repositórios simulados.
6. Homologar em máquina e credenciais dedicadas.
7. Obter autorização separada para ativação e serviço Windows.

Não reutilize o handler para contornar locks, filtros ou regras de negócio dos módulos existentes.

## Rollback

1. Definir `NEXUS_LOCAL_AGENT_ENABLED=false` na VPS e reiniciar somente pelo procedimento aprovado.
2. Encerrar o processo executor Windows.
3. Preservar o SQLite para auditoria; não excluir automaticamente.
4. Reverter o include do router e os arquivos do módulo apenas em mudança de código aprovada.
