#!/usr/bin/env bash
set -euo pipefail

MODE="check"
if [[ "${1:-}" == "--apply" ]]; then MODE="apply"; shift; fi
if [[ $# -ne 0 ]]; then echo "Uso: $0 [--apply]" >&2; exit 2; fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${NEXUS_PROJECT_ROOT:-/home/automacao/bots/ssw_tool}"
PYTHON_BIN="${NEXUS_PYTHON_BIN:-/home/automacao/venv/bin/python}"
ENV_DIR="/etc/nexus"
ENV_FILE="$ENV_DIR/local-agent.env"
DATA_DIR="/var/lib/nexus"
DB_FILE="$DATA_DIR/local-agent.sqlite3"
LOG_DIR="/var/log/nexus"
SERVICE_USER="automacao"

[[ -x "$PYTHON_BIN" ]] || { echo "ERRO: NEXUS_PYTHON_BIN não é executável: $PYTHON_BIN" >&2; exit 1; }
id "$SERVICE_USER" >/dev/null 2>&1 || { echo "ERRO: usuário automacao não existe." >&2; exit 1; }
[[ -f "$PROJECT_ROOT/scripts/run_nexus_server.py" ]] || { echo "ERRO: checkout incompleto." >&2; exit 1; }

if [[ "$MODE" == "apply" ]]; then
  [[ "${EUID}" -eq 0 ]] || { echo "ERRO: --apply exige root." >&2; exit 1; }
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$LOG_DIR"
  install -d -m 0750 -o root -g "$SERVICE_USER" "$ENV_DIR"
  if [[ ! -e "$ENV_FILE" ]]; then
    install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_USER" \
      "$SCRIPT_DIR/local-agent.env.example" "$ENV_FILE"
    echo "CRIADO: $ENV_FILE (preencher segredos; flags continuam false)."
  else
    chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    echo "PRESERVADO: $ENV_FILE"
  fi
  if [[ ! -e "$DB_FILE" ]]; then
    install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_USER" /dev/null "$DB_FILE"
    echo "CRIADO: $DB_FILE"
  else
    chown "$SERVICE_USER:$SERVICE_USER" "$DB_FILE"
    chmod 0600 "$DB_FILE"
    echo "PRESERVADO: $DB_FILE"
  fi
fi

[[ -d "$DATA_DIR" ]] || { echo "PENDENTE: $DATA_DIR"; exit 1; }
[[ -d "$LOG_DIR" ]] || { echo "PENDENTE: $LOG_DIR"; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "PENDENTE: $ENV_FILE"; exit 1; }
[[ "$(stat -c '%U:%G %a' "$ENV_FILE")" == "$SERVICE_USER:$SERVICE_USER 600" ]] || {
  echo "ERRO: esperado automacao:automacao 600 em $ENV_FILE" >&2; exit 1;
}
[[ "$(stat -c '%U:%G %a' "$DATA_DIR")" == "$SERVICE_USER:$SERVICE_USER 750" ]] || {
  echo "ERRO: esperado automacao:automacao 750 em $DATA_DIR" >&2; exit 1;
}
if [[ -e "$DB_FILE" ]]; then
  [[ "$(stat -c '%U:%G %a' "$DB_FILE")" == "$SERVICE_USER:$SERVICE_USER 600" ]] || {
    echo "ERRO: esperado automacao:automacao 600 em $DB_FILE" >&2; exit 1;
  }
fi

echo "OK: pré-requisitos de arquivos e permissões validados."
echo "NÃO EXECUTADO: supervisorctl reread/update/restart."
echo "Revise deploy/local_agent/supervisor-nexus.conf.example antes de alterar o programa ssw_web existente."
