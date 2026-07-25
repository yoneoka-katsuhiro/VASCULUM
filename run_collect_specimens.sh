#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: Python virtual environment was not found: ${VENV_DIR}" >&2
  echo "Run this command once:" >&2
  echo "  bash setup_mac.sh" >&2
  exit 1
fi

export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON}" "${PROJECT_DIR}/scripts/collect_specimens.py" "$@"
