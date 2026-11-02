#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VASCULUM_SETUP_QUIET=1

echo "[1/3] herbarium_specimen_collector: Python environment"
bash "${ROOT_DIR}/herbarium_specimen_collector/setup_mac.sh"
echo "[2/3] checklist: Python environment"
bash "${ROOT_DIR}/checklist/setup_mac.sh"
echo "[3/3] offline release checks"
bash "${ROOT_DIR}/check_release.sh"

echo
echo "VASCULUM setup complete. Next: see 'Quick start' in README.md."
