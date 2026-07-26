#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="${PYTHON_BIN}"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
  PYTHON="${SCRIPT_DIR}/.venv/bin/python"
else
  PYTHON="python3"
fi

export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON}" "$SCRIPT_DIR/scripts/curate_georeferences.py" "$@"
