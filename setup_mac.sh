#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up herbarium_specimen_collector..."
bash "${ROOT_DIR}/herbarium_specimen_collector/setup_mac.sh"

echo
echo "Setting up llm_georeference_curator..."
bash "${ROOT_DIR}/llm_georeference_curator/setup_mac.sh"

echo
bash "${ROOT_DIR}/check_release.sh"

echo
echo "VASCULUM setup complete."
echo "See README.md for independent and combined workflow examples."
