#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${NEXUS_PROJECT_ROOT:-/home/automacao/bots/ssw_tool}"
PYTHON_BIN="${NEXUS_PYTHON_BIN:-/home/automacao/venv/bin/python}"
ENV_FILE="${1:-/etc/nexus/local-agent.env}"

[[ -x "$PYTHON_BIN" ]] || { echo "ERRO: NEXUS_PYTHON_BIN não é executável: $PYTHON_BIN" >&2; exit 1; }
[[ -f "$PROJECT_ROOT/scripts/nexus_local_agent_diagnose.py" ]] || { echo "ERRO: checkout não encontrado: $PROJECT_ROOT" >&2; exit 1; }
if [[ "$(id -un)" == "automacao" ]]; then
  "$PYTHON_BIN" "$PROJECT_ROOT/scripts/nexus_local_agent_diagnose.py" \
    --role server --env-file "$ENV_FILE"
else
  command -v sudo >/dev/null || { echo "ERRO: sudo não encontrado." >&2; exit 1; }
  sudo -u automacao -- "$PYTHON_BIN" \
    "$PROJECT_ROOT/scripts/nexus_local_agent_diagnose.py" \
    --role server --env-file "$ENV_FILE"
fi
if command -v supervisorctl >/dev/null; then
  set +e
  supervisor_output="$(supervisorctl status ssw_web 2>&1)"
  supervisor_status=$?
  set -e
  if [[ $supervisor_status -eq 0 ]]; then
    echo "OK: programa Supervisor 'ssw_web' detectado."
  else
    echo "AVISO: não foi possível consultar 'ssw_web' no Supervisor (permissão ou indisponibilidade)."
  fi
else
  echo "AVISO: supervisorctl não encontrado."
fi
echo "Diagnóstico somente leitura concluído; nenhum segredo foi exibido."
