#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found." >&2
  exit 1
fi

python3 -m venv .venv

if grep -Eq '^[[:space:]]*[^#[:space:]]' requirements.txt; then
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "llm_georeference_curator setup complete."
echo "Run: ${SCRIPT_DIR}/run_llm_georeference_curator.sh --help"
