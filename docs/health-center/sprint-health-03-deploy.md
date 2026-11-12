# Sprint Health 03 — deploy controlado

## Escopo

O monitor de dependências é um worker opt-in do Health Center. Ele não é iniciado
pelo FastAPI, não importa bots e não modifica schedulers. O catálogo inicial cobre
somente alcance técnico do SSW por DNS, TCP, TLS e HTTP HEAD/GET sem login.

## Pré-requisitos

1. Confirmar backup, schema `automationx` e usuário de migration.
2. Revisar `002_create_dependency_health_tables.sql` no ambiente alvo.
3. Manter `HEALTH_DEPENDENCY_MONITOR_ENABLED=false` durante a migration.
4. Configurar uma allowlist exata; lista vazia bloqueia qualquer destino.
5. Não reutilizar usuário, senha, cookie ou token do SSW nos probes.

## Migration manual

Não é executada no startup. Após confirmar explicitamente o schema descartável ou
o schema `automationx` autorizado, executar com um arquivo de opções protegido:

```bash
mysql --defaults-extra-file=/caminho/seguro/mysql-migration.cnf \
  automationx < modules/health_center/migrations/002_create_dependency_health_tables.sql
```

A migration cria somente tabelas `health_dependenc*` e a tabela de associação
`health_execution_dependency_incidents`. Não altera tabelas anteriores.

## Configuração inicial SSW

```dotenv
HEALTH_DEPENDENCY_MONITOR_ENABLED=false
HEALTH_DEPENDENCY_SSW_ENABLED=false
HEALTH_DEPENDENCY_SSW_URL=https://host-autorizado/caminho-passivo
HEALTH_DEPENDENCY_SSW_HTTP_METHOD=HEAD
HEALTH_DEPENDENCY_ALLOWED_HOSTS=host-autorizado
```

Habilitar primeiro em QA. HTTP 2xx representa alcance técnico e não comprova login
ou disponibilidade funcional. Redirecionamentos não são seguidos.

## Exemplo controlado para produção

As variáveis fornecidas diretamente pelo Supervisor têm precedência sobre o `.env`.
O entrypoint usa `load_dotenv(..., override=False)` e não registra URL, host,
credenciais ou conteúdo de exceções.

```dotenv
HEALTH_DEPENDENCY_MONITOR_ENABLED=true
HEALTH_DEPENDENCY_SSW_ENABLED=true
HEALTH_DEPENDENCY_SSW_URL=https://host-autorizado/caminho-passivo
HEALTH_DEPENDENCY_SSW_HTTP_METHOD=HEAD
HEALTH_DEPENDENCY_ALLOWED_HOSTS=host-autorizado
HEALTH_DEPENDENCY_INTERVAL_SECONDS=300
HEALTH_DEPENDENCY_TIMEOUT_SECONDS=5
HEALTH_DEPENDENCY_MAX_CONCURRENCY=1
HEALTH_DEPENDENCY_RETRIES=0
HEALTH_DEPENDENCY_FAILURE_THRESHOLD=3
HEALTH_DEPENDENCY_RECOVERY_THRESHOLD=2
HEALTH_DEPENDENCY_STALE_AFTER_SECONDS=900
HEALTH_DEPENDENCY_ALERT_COOLDOWN_SECONDS=1800
```

Os logs são eventos JSON sanitizados. Falhas internas do processo são classificadas
como `UNKNOWN` e nunca como indisponibilidade comprovada da dependência.

## Worker

Somente depois da migration e da validação da allowlist:

```bash
.venv/bin/python scripts/run_dependency_monitor.py
```

O comando apenas executa probes e persiste observabilidade. Não inicia, interrompe,
reagenda ou executa automações. Integração com Supervisor fica fora desta entrega.

## Rollback operacional

Definir `HEALTH_DEPENDENCY_MONITOR_ENABLED=false` e encerrar somente o worker do
monitor. Não remover tabelas automaticamente e não reiniciar a aplicação principal.
