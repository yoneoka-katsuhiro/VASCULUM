#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_DIR="${ROOT_DIR}/herbarium_specimen_collector"
CURATOR_DIR="${ROOT_DIR}/llm_georeference_curator"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    "Usage: bash run_collect_and_georeference.sh [collector options] -- [curator options]" \
    "" \
    "Runs herbarium_specimen_collector first, then passes its output directory to llm_georeference_curator." \
    "" \
    "Collector help:" \
    "  bash herbarium_specimen_collector/run_collect_specimens.sh --help" \
    "" \
    "Curator help:" \
    "  bash llm_georeference_curator/run_llm_georeference_curator.sh --help"
  exit 0
fi

COLLECT_ARGS=()
CURATOR_ARGS=()
TARGET="collector"

for arg in "$@"; do
  if [[ "${arg}" == "--" ]]; then
    TARGET="curator"
    continue
  fi
  if [[ "${TARGET}" == "collector" ]]; then
    COLLECT_ARGS+=("${arg}")
  else
    CURATOR_ARGS+=("${arg}")
  fi
done

TMP_OUTPUT="$(mktemp)"
trap 'rm -f "${TMP_OUTPUT}"' EXIT

bash "${COLLECTOR_DIR}/run_collect_specimens.sh" "${COLLECT_ARGS[@]}" | tee "${TMP_OUTPUT}"

COLLECTOR_OUTPUT="$(
  awk -F 'Output: ' '/^Output: / {print $2; exit}' "${TMP_OUTPUT}"
)"

if [[ -z "${COLLECTOR_OUTPUT}" ]]; then
  echo "ERROR: collector output directory could not be detected." >&2
  exit 1
fi

exec bash "${CURATOR_DIR}/run_llm_georeference_curator.sh" \
  --input "${COLLECTOR_OUTPUT}" \
  "${CURATOR_ARGS[@]}"
