#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_DIR="${ROOT_DIR}/herbarium_specimen_collector"
CURATOR_DIR="${ROOT_DIR}/llm_georeference_curator"
COLLECTOR_PYTHON="${COLLECTOR_DIR}/.venv/bin/python"
CURATOR_PYTHON="${CURATOR_DIR}/.venv/bin/python"

if [[ ! -x "${COLLECTOR_PYTHON}" ]]; then
  COLLECTOR_PYTHON="$(command -v python3 || true)"
fi
if [[ ! -x "${CURATOR_PYTHON}" ]]; then
  CURATOR_PYTHON="$(command -v python3 || true)"
fi
if [[ -z "${COLLECTOR_PYTHON}" || -z "${CURATOR_PYTHON}" ]]; then
  echo "ERROR: python3 was not found." >&2
  exit 1
fi

for required_pattern in ".env" ".venv/" "__pycache__/" ".DS_Store" "output/"; do
  if ! grep -Fqx "${required_pattern}" "${ROOT_DIR}/.gitignore"; then
    echo "ERROR: .gitignore is missing: ${required_pattern}" >&2
    exit 1
  fi
done

echo "Checking shell scripts..."
while IFS= read -r script; do
  bash -n "${script}"
done < <(find "${ROOT_DIR}" -path "*/.venv" -prune -o -name "*.sh" -type f -print)

PYCACHE_DIR="$(mktemp -d)"
trap 'rm -rf "${PYCACHE_DIR}"' EXIT
export PYTHONPYCACHEPREFIX="${PYCACHE_DIR}"

echo "Compiling Python files..."
"${COLLECTOR_PYTHON}" -m compileall -q \
  "${COLLECTOR_DIR}/scripts" "${COLLECTOR_DIR}/tests"
"${CURATOR_PYTHON}" -m compileall -q \
  "${CURATOR_DIR}/scripts" "${CURATOR_DIR}/tests"

echo "Running collector offline tests..."
"${COLLECTOR_PYTHON}" "${COLLECTOR_DIR}/tests/test_offline.py"

echo "Running georeference curator offline tests..."
"${CURATOR_PYTHON}" "${CURATOR_DIR}/tests/test_offline.py"

echo "Checking CLI entry points..."
bash "${COLLECTOR_DIR}/run_collect_specimens.sh" --help >/dev/null
bash "${CURATOR_DIR}/run_llm_georeference_curator.sh" --help >/dev/null

if command -v rg >/dev/null 2>&1; then
  if rg -n \
    --glob '!**/.venv/**' \
    --glob '!**/.git/**' \
    --glob '!**/output/**' \
    --glob '!**/.env.example' \
    'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}' \
    "${ROOT_DIR}" >/dev/null; then
    echo "ERROR: a possible credential or private key was found." >&2
    exit 1
  fi
else
  if grep -ER \
    --exclude-dir=.venv \
    --exclude-dir=.git \
    --exclude-dir=output \
    --exclude=.env.example \
    'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}' \
    "${ROOT_DIR}" >/dev/null; then
    echo "ERROR: a possible credential or private key was found." >&2
    exit 1
  fi
fi

echo "Release checks passed."
