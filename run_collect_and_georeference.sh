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

set +e
bash "${COLLECTOR_DIR}/run_collect_specimens.sh" "${COLLECT_ARGS[@]}" | tee "${TMP_OUTPUT}"
COLLECTOR_STATUS="${PIPESTATUS[0]}"
set -e

COLLECTOR_OUTPUT="$(
  awk -F 'Output: ' '/^Output: / {print $2; exit}' "${TMP_OUTPUT}"
)"

if [[ -z "${COLLECTOR_OUTPUT}" ]]; then
  echo "ERROR: collector output directory could not be detected." >&2
  exit "${COLLECTOR_STATUS:-1}"
fi

CURATOR_YES="false"
for arg in "${CURATOR_ARGS[@]}"; do
  if [[ "${arg}" == "--yes" ]]; then
    CURATOR_YES="true"
    break
  fi
done

if [[ "${COLLECTOR_STATUS}" -ne 0 ]]; then
  echo
  echo "Collector finished with partial errors, but usable output was created:"
  echo "  ${COLLECTOR_OUTPUT}"
  echo "Some records or images may be missing. See summary.txt for details."
  if [[ "${CURATOR_YES}" == "true" ]]; then
    echo "Continuing because curator option --yes was supplied."
  else
    read -r -p "Continue to LLM georeference curation? [y/n]: " REPLY
    case "${REPLY}" in
      y|Y|yes|YES)
        ;;
      *)
        echo "Stopped before LLM georeference curation."
        exit "${COLLECTOR_STATUS}"
        ;;
    esac
  fi
fi

exec bash "${CURATOR_DIR}/run_llm_georeference_curator.sh" \
  --input "${COLLECTOR_OUTPUT}" \
  "${CURATOR_ARGS[@]}"
