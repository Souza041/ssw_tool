#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${1:-/etc/nexus/local-agent.env}"

command -v python3 >/dev/null || { echo "ERRO: python3 não encontrado." >&2; exit 1; }
[[ -x "$PROJECT_ROOT/.venv/bin/python" ]] || { echo "ERRO: Python do venv não encontrado." >&2; exit 1; }
if [[ "$(id -un)" == "automacao" ]]; then
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/nexus_local_agent_diagnose.py" \
    --role server --env-file "$ENV_FILE"
else
  command -v sudo >/dev/null || { echo "ERRO: sudo não encontrado." >&2; exit 1; }
  sudo -u automacao -- "$PROJECT_ROOT/.venv/bin/python" \
    "$PROJECT_ROOT/scripts/nexus_local_agent_diagnose.py" \
    --role server --env-file "$ENV_FILE"
fi
if command -v supervisorctl >/dev/null; then
  supervisorctl status nexus 2>/dev/null || echo "AVISO: programa Supervisor 'nexus' não localizado."
else
  echo "AVISO: supervisorctl não encontrado."
fi
echo "Diagnóstico somente leitura concluído; nenhum segredo foi exibido."
