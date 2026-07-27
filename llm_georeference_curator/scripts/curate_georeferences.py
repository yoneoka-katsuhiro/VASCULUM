#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from georeference_curator.pipeline import run_pipeline


PROJECT_DIR = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "VASCULUM llm_georeference_curator: LLM-assisted georeferencing "
            "for herbarium_specimen_collector DwC outputs."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Collector output directory containing dwc.tsv/dwc.csv and images/.")
    parser.add_argument("--input-dwc", type=Path, help="Explicit DwC CSV/TSV file. Defaults to <input>/dwc.tsv then dwc.csv.")
    parser.add_argument("--output", type=Path, help="Output directory. Default: output/<input_name>_georeferenced.")
    parser.add_argument("--label-tsv", type=Path, help="Optional TSV/CSV with label transcriptions keyed by catalogNumber.")
    parser.add_argument("--gazetteer-tsv", type=Path, help="Optional local gazetteer TSV/CSV for locality-string geocoding.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input files and options without writing output.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N records. Useful for LLM smoke tests.")

    parser.add_argument(
        "--curation-mode",
        choices=("standard", "robust"),
        default="standard",
        help=(
            "standard keeps coarse original coordinates as reviewable final values "
            "when no better candidate exists. robust records coarse originals only "
            "as candidates and leaves final DwC coordinates blank unless corroborated."
        ),
    )
    parser.add_argument("--standard", dest="curation_mode", action="store_const", const="standard", help="Alias for --curation-mode standard.")
    parser.add_argument("--robust", dest="curation_mode", action="store_const", const="robust", help="Alias for --curation-mode robust.")
    parser.add_argument("--original-precision-decimals", type=int, default=4, help="Minimum decimal places required to treat original coordinates as precise.")
    parser.add_argument("--review-distance-km", type=float, default=5.0, help="Distance at which precise original and candidate coordinates conflict.")
    parser.add_argument(
        "--exclude-insufficient-locality",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exclude records when locality is only country/state/province level and no image/candidate is available.",
    )

    parser.add_argument("--llm-mode", choices=("auto", "on", "off"), default="auto", help="auto uses an available provider, on requires it, off disables LLM use.")
    parser.add_argument(
        "--llm-provider",
        choices=("codex-cli", "codex", "openai", "opus", "opus5", "fable5", "custom-cli"),
        default="codex-cli",
        help="Default codex-cli uses saved Codex/ChatGPT login through codex exec.",
    )
    parser.add_argument("--llm-model", default="auto", help="Model name or auto. Provider candidates can be set with CODEX_MODEL_CANDIDATES, OPUS_MODEL_CANDIDATES, etc.")
    parser.add_argument("--llm-reasoning-effort", default="", help="Reasoning effort. Default: high or environment override.")
    parser.add_argument(
        "--llm-web-search",
        choices=("live", "cached", "indexed", "disabled"),
        default="live",
        help=(
            "Web research mode. Search stops after the first resolved language stage; "
            "Chinese fallback is used only when the specimen context makes it relevant."
        ),
    )
    parser.add_argument("--llm-command", default="", help="Command for opus/fable/custom providers. Placeholders: {model}, {prompt_file}, {image_paths}.")
    parser.add_argument("--llm-api-key-env", default="OPENAI_API_KEY", help="Environment variable containing the OpenAI API key.")
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=600,
        help="Legacy fallback timeout for stages without an explicit stage timeout.",
    )
    parser.add_argument(
        "--label-timeout-seconds",
        type=int,
        help="Whole-image label-reading stage timeout. Default: 600.",
    )
    parser.add_argument(
        "--georeference-timeout-seconds",
        type=int,
        help="LLM coordinate-research stage timeout. Default: 600.",
    )
    parser.add_argument(
        "--verification-timeout-seconds",
        type=int,
        help="Terrain, habitat, route, and DEM verification stage timeout. Default: 600.",
    )
    parser.add_argument(
        "--llm-rate-limit-retries",
        type=int,
        default=2,
        help="Retry only rate-limit/usage-limit LLM failures with exponential backoff.",
    )
    parser.add_argument(
        "--workers",
        default="auto",
        help=(
            "Parallel record workers: auto, max, or a positive integer. "
            "auto respects provider/model/search weight and Codex config; max uses the configured cap."
        ),
    )
    parser.add_argument(
        "--llm-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse matching transcription/georeference responses on later runs.",
    )
    parser.add_argument(
        "--llm-cache-dir",
        type=Path,
        help=(
            "Persistent LLM response cache directory. "
            "Default: <pipeline>/.cache/llm_georeference_curator."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation before real LLM and external geospatial service calls.")

    parser.add_argument("--georeferenced-by", default="VASCULUM llm_georeference_curator", help="Value written to DwC georeferencedBy.")
    parser.add_argument("--prompt-profile", choices=("vasculum-default", "xie", "xie-modified"), default="xie-modified", help="Prompt/protocol profile recorded in georeferenceProtocol.")
    parser.add_argument("--use-trails", action=argparse.BooleanOptionalAction, default=True, help="Use roads and trails as georeferencing evidence.")
    parser.add_argument("--use-hydrology", action=argparse.BooleanOptionalAction, default=True, help="Use rivers, streams, valleys, and hydrology as evidence.")
    parser.add_argument("--use-dem", action=argparse.BooleanOptionalAction, default=True, help="Use elevation and terrain as georeferencing evidence.")
    parser.add_argument(
        "--geospatial-refinement",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Validate candidates against OSM environmental context and a 90 m DEM; "
            "refine route-supported anchors."
        ),
    )
    parser.add_argument("--use-vegetation-prior", action=argparse.BooleanOptionalAction, default=True, help="Use vegetation/habitat as supporting evidence.")
    parser.add_argument(
        "--habitat",
        action="append",
        default=[],
        help=(
            "Taxon habitat constraint; repeat as needed, e.g. "
            "--habitat 'subalpine forest' --habitat river."
        ),
    )
    parser.add_argument(
        "--taxon-habitat",
        default="",
        help="Legacy free-text habitat option; combined with --habitat.",
    )
    parser.add_argument("--debug-log", action=argparse.BooleanOptionalAction, default=True, help="Write georeference.log.jsonl.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    habitat_inputs = list(args.habitat)
    if args.taxon_habitat.strip():
        habitat_inputs.append(args.taxon_habitat.strip())
    if args.original_precision_decimals < 0:
        print("ERROR: --original-precision-decimals must be non-negative.", file=sys.stderr)
        return 2
    if args.review_distance_km <= 0:
        print("ERROR: --review-distance-km must be greater than 0.", file=sys.stderr)
        return 2
    if args.llm_timeout_seconds <= 0:
        print("ERROR: --llm-timeout-seconds must be greater than 0.", file=sys.stderr)
        return 2
    for option, value in (
        ("--label-timeout-seconds", args.label_timeout_seconds),
        ("--georeference-timeout-seconds", args.georeference_timeout_seconds),
        ("--verification-timeout-seconds", args.verification_timeout_seconds),
    ):
        if value is not None and value <= 0:
            print(f"ERROR: {option} must be greater than 0.", file=sys.stderr)
            return 2
    if args.llm_rate_limit_retries < 0:
        print("ERROR: --llm-rate-limit-retries must be non-negative.", file=sys.stderr)
        return 2
    if args.limit < 0:
        print("ERROR: --limit must be non-negative.", file=sys.stderr)
        return 2

    try:
        report = run_pipeline(
            project_dir=PROJECT_DIR,
            input_dir=args.input,
            input_dwc=args.input_dwc,
            output_dir=args.output,
            label_tsv=args.label_tsv,
            gazetteer_tsv=args.gazetteer_tsv,
            dry_run=args.dry_run,
            curation_mode=args.curation_mode,
            original_precision_decimals=args.original_precision_decimals,
            review_distance_km=args.review_distance_km,
            exclude_insufficient_locality=args.exclude_insufficient_locality,
            llm_mode=args.llm_mode,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_web_search=args.llm_web_search,
            llm_command=args.llm_command,
            llm_api_key_env=args.llm_api_key_env,
            llm_timeout_seconds=args.llm_timeout_seconds,
            llm_rate_limit_retries=args.llm_rate_limit_retries,
            confirm_llm=not args.yes,
            georeferenced_by=args.georeferenced_by,
            prompt_profile=args.prompt_profile,
            use_trails=args.use_trails,
            use_hydrology=args.use_hydrology,
            use_dem=args.use_dem,
            geospatial_refinement=args.geospatial_refinement,
            use_vegetation_prior=args.use_vegetation_prior,
            taxon_habitat=" | ".join(habitat_inputs),
            debug_log=args.debug_log,
            limit=args.limit,
            workers=args.workers,
            llm_cache_enabled=args.llm_cache,
            llm_cache_dir=args.llm_cache_dir,
            label_timeout_seconds=args.label_timeout_seconds,
            georeference_timeout_seconds=args.georeference_timeout_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
        )
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"Input validated: {report.records_read} DwC record(s), "
            f"{report.label_sidecar_rows} label sidecar row(s), "
            f"habitat={report.habitat_prior or 'none'}. No files were written."
        )
        return 0

    print(f"Output: {report.output_dir}")
    print(f"Modified DwC CSV: {report.output_dir / 'modified_dwc.csv'}")
    print(f"Modified DwC TSV: {report.output_dir / 'modified_dwc.tsv'}")
    print(f"Candidates TSV: {report.output_dir / 'georeference_candidates.tsv'}")
    print(f"Summary: {report.output_dir / 'summary.txt'}")
    if args.debug_log:
        print(f"Log: {report.output_dir / 'georeference.log.jsonl'}")
    if report.partial_failure:
        print(f"Completed with {len(report.errors)} error(s); see summary.txt.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
