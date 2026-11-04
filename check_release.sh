#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${VASCULUM_PYTHON:-${ROOT_DIR}/herbarium_specimen_collector/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
for script in "${ROOT_DIR}"/*.sh "${ROOT_DIR}"/checklist/*.sh "${ROOT_DIR}"/herbarium_specimen_collector/*.sh; do
  bash -n "${script}"
done
exec "${PYTHON_BIN}" "${ROOT_DIR}/scripts/check_release.py"
