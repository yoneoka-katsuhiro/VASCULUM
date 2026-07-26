#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 was not found." >&2
  echo "Install Python 3 first, for example with Homebrew:" >&2
  echo "  brew install python" >&2
  exit 1
fi

python3 -m venv "${VENV_DIR}"

if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
  "${VENV_DIR}/bin/python" -m ensurepip --upgrade
fi

PIP_DISABLE_PIP_VERSION_CHECK=1 \
  "${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt"

echo
echo "Setup finished."
echo "Next commands:"
echo "  bash run_collect_specimens.sh --contact-email \"your.email@example.com\" --dry-run --taxon \"Haplopteris mediosora\" --synonym \"Vittaria mediosora\""
echo "  bash run_collect_specimens.sh --contact-email \"your.email@example.com\" --taxon \"Haplopteris mediosora\" --synonym \"Vittaria mediosora\" --image-resolution standard"
