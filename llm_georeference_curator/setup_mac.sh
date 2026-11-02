#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v Rscript >/dev/null 2>&1; then
  echo "Error: R is required for the ribbon PDF. Install R from https://cran.r-project.org/, then rerun setup." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found." >&2
  exit 1
fi

python3 -m venv .venv

if grep -Eq '^[[:space:]]*[^#[:space:]]' requirements.txt; then
  .venv/bin/python -m pip install -r requirements.txt
fi

mkdir -p .Rlibrary
R_LIBS_USER="${SCRIPT_DIR}/.Rlibrary${R_LIBS_USER:+:${R_LIBS_USER}}" Rscript --vanilla -e \
  'p <- c("ggplot2", "ggalluvial"); missing <- p[!vapply(p, requireNamespace, logical(1), quietly=TRUE)]; if(length(missing)) install.packages(missing, lib=.libPaths()[1], repos="https://cloud.r-project.org"); stopifnot(all(vapply(p, requireNamespace, logical(1), quietly=TRUE)))'

echo "llm_georeference_curator setup complete."
echo "Run: ${SCRIPT_DIR}/run_llm_georeference_curator.sh --help"
