#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VASCULUM_SETUP_QUIET=1

echo "[1/4] herbarium_specimen_collector: Python environment"
bash "${ROOT_DIR}/herbarium_specimen_collector/setup_mac.sh"
echo "[2/4] llm_georeference_curator: Python environment and R plotting packages"
bash "${ROOT_DIR}/llm_georeference_curator/setup_mac.sh"
echo "[3/4] checklist: Python environment"
bash "${ROOT_DIR}/checklist/setup_mac.sh"
echo "[4/4] offline release checks"
bash "${ROOT_DIR}/check_release.sh"

echo
echo "VASCULUM setup complete. Next: see 'Quick start' in README.md."
