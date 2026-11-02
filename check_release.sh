#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_DIR="${ROOT_DIR}/herbarium_specimen_collector"
CURATOR_DIR="${ROOT_DIR}/llm_georeference_curator"
CHECKLIST_DIR="${ROOT_DIR}/checklist"
COLLECTOR_PYTHON="${COLLECTOR_DIR}/.venv/bin/python"
CURATOR_PYTHON="${CURATOR_DIR}/.venv/bin/python"
CHECKLIST_PYTHON="${CHECKLIST_DIR}/.venv/bin/python"

if [[ ! -x "${COLLECTOR_PYTHON}" ]]; then
  COLLECTOR_PYTHON="$(command -v python3 || true)"
fi
if [[ ! -x "${CURATOR_PYTHON}" ]]; then
  CURATOR_PYTHON="$(command -v python3 || true)"
fi
if [[ ! -x "${CHECKLIST_PYTHON}" ]]; then
  CHECKLIST_PYTHON="$(command -v python3 || true)"
fi
if [[ -z "${COLLECTOR_PYTHON}" || -z "${CURATOR_PYTHON}" || -z "${CHECKLIST_PYTHON}" ]]; then
  echo "ERROR: python3 was not found." >&2
  exit 1
fi

for required_pattern in ".env" ".venv/" "__pycache__/" ".DS_Store" "output/"; do
  if ! grep -Fqx "${required_pattern}" "${ROOT_DIR}/.gitignore"; then
    echo "ERROR: .gitignore is missing: ${required_pattern}" >&2
    exit 1
  fi
done

START_SECONDS="${SECONDS}"
TOTAL_TESTS=0
LOG_FILE="$(mktemp)"
PYCACHE_DIR="$(mktemp -d)"
trap 'rm -rf "${PYCACHE_DIR}" "${LOG_FILE}"' EXIT
export PYTHONPYCACHEPREFIX="${PYCACHE_DIR}"

# Run one check quietly; print its output only when it fails.
run_check() {
  local label="$1"
  shift
  if ! "$@" >"${LOG_FILE}" 2>&1; then
    echo "FAILED: ${label}" >&2
    cat "${LOG_FILE}" >&2
    exit 1
  fi
  local count
  count="$(sed -n 's/^Ran \([0-9][0-9]*\) tests\{0,1\} in .*/\1/p' "${LOG_FILE}" | tail -n 1)"
  TOTAL_TESTS=$((TOTAL_TESTS + ${count:-0}))
}

while IFS= read -r script; do
  run_check "shell syntax ${script}" bash -n "${script}"
done < <(find "${ROOT_DIR}" -path "*/.venv" -prune -o -name "*.sh" -type f -print)

run_check "compile collector" "${COLLECTOR_PYTHON}" -m compileall -q "${COLLECTOR_DIR}/scripts" "${COLLECTOR_DIR}/tests"
run_check "compile curator" "${CURATOR_PYTHON}" -m compileall -q "${CURATOR_DIR}/scripts" "${CURATOR_DIR}/tests"
run_check "compile checklist" "${CHECKLIST_PYTHON}" -m compileall -q "${CHECKLIST_DIR}/scripts"

for test_file in "${COLLECTOR_DIR}"/tests/test_*.py; do
  run_check "${test_file#"${ROOT_DIR}/"}" "${COLLECTOR_PYTHON}" "${test_file}"
done
for test_file in "${CURATOR_DIR}"/tests/test_*.py; do
  run_check "${test_file#"${ROOT_DIR}/"}" "${CURATOR_PYTHON}" "${test_file}"
done

run_check "collector --help" bash "${COLLECTOR_DIR}/run_collect_specimens.sh" --help
run_check "curator --help" bash "${CURATOR_DIR}/run_llm_georeference_curator.sh" --help
run_check "checklist --help" bash "${CHECKLIST_DIR}/run_checklist.sh" --help
run_check "combined wrapper --help" bash "${ROOT_DIR}/run_collect_and_georeference.sh" --help

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

echo "Release checks passed: ${TOTAL_TESTS} tests, shell syntax, compilation, CLI entry points, credential scan ($((SECONDS - START_SECONDS))s)."
