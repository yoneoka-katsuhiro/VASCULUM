#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from georeference_curator.pipeline import display_path, run_pipeline
from georeference_curator.progress import TerminalProgress, format_duration, format_radius, format_tokens


PROJECT_DIR = Path(__file__).resolve().parents[1]


def print_run_summary(report, *, export_images=False):
    """Three-line run summary on stderr; details stay in summary.txt."""
    elapsed = (report.finished_at - report.started_at).total_seconds() if report.finished_at else 0
    tokens = report.llm_input_tokens + report.llm_output_tokens
    failed = f" · failed {report.failed} (rerun the same command to retry)" if report.failed else ""
    print(f"\nDone {report.records_read} → accepted {report.accepted} · excluded {report.excluded}{failed}"
          f" · {format_duration(elapsed)} · {format_tokens(tokens)} tok", file=sys.stderr)
    if report.accepted:
        counts = " · ".join(f"{level} {report.confidence_counts.get(level, 0)}" for level in ("high", "medium", "low"))
        print(f"confidence {counts} · median radius {format_radius(report.median_uncertainty_meters)}", file=sys.stderr)
    files = ["modified_dwc.csv/.tsv", "georeference_candidates.tsv", "summary.txt"]
    files += ["georeference_flow.pdf"] if report.ribbon_path else []
    files += [f"images/ ({report.images_exported})"] if report.images_exported or export_images else []
    files.append("georeference.log.jsonl")
    print(f"files in {display_path(report.output_dir)}: " + ", ".join(files), file=sys.stderr)
    for error in report.errors[:5]:
        print(f"error: {error}", file=sys.stderr)
    if len(report.errors) > 5:
        print(f"… {len(report.errors) - 5} more errors in summary.txt", file=sys.stderr)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "VASCULUM llm_georeference_curator: LLM-assisted georeferencing "
            "for herbarium_specimen_collector DwC outputs. "
            "Keep documented coordinates or georeference readable specimen photos. "
            "Final DwC includes accepted records only; every record remains in audit outputs."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Collector output directory containing dwc.tsv/dwc.csv and images/.")
    parser.add_argument("--input-dwc", type=Path, help="Explicit DwC CSV/TSV file. Defaults to <input>/dwc.tsv then dwc.csv.")
    parser.add_argument("--additional-input", type=Path, action="append", default=[], help="Additional personal DwC CSV/TSV or directory with dwc.tsv/dwc.csv and/or images/. Repeat for multiple sources.")
    parser.add_argument("--include-personal", action=argparse.BooleanOptionalAction, default=True, help="Automatically include <input>/personal/dwc.tsv or dwc.csv and unlinked photographs in personal/images/.")
    parser.add_argument("--import-unlinked-images", action="store_true", help="Also create records for otherwise unlinked images in the collector images/ directory. Personal images are included by default.")
    parser.add_argument("--export-images", action="store_true", help="Copy only final included specimen images into output/images/. miss_ specimens are excluded; future reruns keep managed exports synchronized.")
    parser.add_argument("--output", type=Path, help="Output directory. Default: output/<input_name>_georeferenced.")
    parser.add_argument("--label-tsv", type=Path, help="Optional TSV/CSV with label transcriptions keyed by catalogNumber.")
    parser.add_argument("--gazetteer-tsv", type=Path, help="Optional local gazetteer TSV/CSV for locality-string geocoding.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input files and options without writing output.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N records. Useful for LLM smoke tests.")

    parser.add_argument("--max-uncertainty-meters", type=float, help="Optional study-specific maximum accepted horizontal uncertainty, not an accuracy guarantee.")
    parser.add_argument("--label-coordinate-uncertainty-meters", type=float, help="Explicit measurement allowance added to label rounding uncertainty; document the justification for this policy.")
    parser.add_argument("--reference-regions-tsv", type=Path, help="Optional country/state bounding regions with source provenance for consistency checks.")
    parser.add_argument("--review-distance-km", type=float, default=5.0, help="Distance at which precise original and candidate coordinates conflict.")

    parser.add_argument("--llm-mode", choices=("auto", "on", "off"), default="auto", help="auto uses an available provider, on requires it, off disables LLM use.")
    parser.add_argument(
        "--llm-provider",
        choices=("codex-cli", "codex", "claude-cli", "claude", "openai", "opus", "opus5", "fable5", "custom-cli"),
        default="codex-cli",
        help="codex-cli (default) uses your ChatGPT login through codex exec; claude uses your Claude login through Claude Code (claude -p).",
    )
    parser.add_argument("--llm-model", default="auto", help="Model name or auto (codex: gpt-6.1-sol; claude: opus). Candidates can be set with CODEX_MODEL_CANDIDATES, CLAUDE_MODEL_CANDIDATES, etc.")
    parser.add_argument("--llm-reasoning-effort", default="", help="Coordinate-research reasoning effort. Default: medium or environment override.")
    parser.add_argument("--label-model", default="", help="Label-reading model. Default: same as --llm-model, or LLM_LABEL_MODEL.")
    parser.add_argument("--label-reasoning-effort", default="", help="Label-reading effort. Default: medium or LLM_LABEL_REASONING_EFFORT.")
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
        "--label-timeout-seconds",
        type=int,
        default=600,
        help="Whole-image label-reading stage timeout. Default: 600.",
    )
    parser.add_argument(
        "--georeference-timeout-seconds",
        type=int,
        default=600,
        help="LLM coordinate-research stage timeout. Default: 600.",
    )
    parser.add_argument(
        "--verification-timeout-seconds",
        type=int,
        default=600,
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
    parser.add_argument("--verbose", action="store_true", help="Also print each processing stage and run details; by default the terminal shows one line per specimen.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Reuse completed record checkpoints when inputs/code/settings match.")
    parser.add_argument("--verify-source-urls", action=argparse.BooleanOptionalAction, default=True, help="Retrieve bounded source snapshots; reachability is not factual verification.")
    parser.add_argument("--refresh-sources", action="store_true", help="Refresh source snapshots and bypass completed-record checkpoints.")
    parser.add_argument("--max-llm-calls", type=int, default=0, help="Hard run-wide fresh LLM attempt limit, including retries. 0 means no extra limit; not a token cap.")

    parser.add_argument("--georeferenced-by", default="VASCULUM llm_georeference_curator", help="Value written to DwC georeferencedBy.")
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
    parser.add_argument("--use-vegetation-prior", action=argparse.BooleanOptionalAction, default=True, help="Use vegetation/habitat explicitly recorded for the collecting event as locality evidence.")
    parser.add_argument(
        "--habitat",
        action="append",
        default=[],
        help=(
            "Taxon habitat for advisory review only; does not move or exclude coordinates. Repeat, e.g. "
            "--habitat 'subalpine forest' --habitat river."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    habitat_inputs = list(args.habitat)
    if args.review_distance_km <= 0:
        print("ERROR: --review-distance-km must be greater than 0.", file=sys.stderr)
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
            review_distance_km=args.review_distance_km,
            llm_mode=args.llm_mode,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_reasoning_effort=args.llm_reasoning_effort,
            llm_web_search=args.llm_web_search,
            llm_command=args.llm_command,
            llm_api_key_env=args.llm_api_key_env,
            llm_rate_limit_retries=args.llm_rate_limit_retries,
            confirm_llm=not args.yes,
            georeferenced_by=args.georeferenced_by,
            use_trails=args.use_trails,
            use_hydrology=args.use_hydrology,
            use_dem=args.use_dem,
            geospatial_refinement=args.geospatial_refinement,
            use_vegetation_prior=args.use_vegetation_prior,
            taxon_habitat=" | ".join(habitat_inputs),
            limit=args.limit,
            workers=args.workers,
            llm_cache_enabled=args.llm_cache,
            llm_cache_dir=args.llm_cache_dir,
            label_timeout_seconds=args.label_timeout_seconds,
            georeference_timeout_seconds=args.georeference_timeout_seconds,
            verification_timeout_seconds=args.verification_timeout_seconds,
            label_model=args.label_model,
            label_reasoning_effort=args.label_reasoning_effort,
            max_uncertainty_meters=args.max_uncertainty_meters,
            label_coordinate_uncertainty_meters=args.label_coordinate_uncertainty_meters,
            reference_regions_tsv=args.reference_regions_tsv,
            resume=args.resume,
            verify_source_urls=args.verify_source_urls,
            refresh_sources=args.refresh_sources,
            max_llm_calls=args.max_llm_calls,
            additional_inputs=args.additional_input,
            include_personal=args.include_personal,
            import_unlinked_images=args.import_unlinked_images,
            export_images=args.export_images,
            progress=TerminalProgress(verbose=args.verbose),
        )
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. All record workers stopped; completed checkpoints and transcription caches are preserved.", file=sys.stderr)
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

    print_run_summary(report, export_images=args.export_images)
    # stdout carries only the output directory, so wrappers can capture it.
    print(f"Output: {report.output_dir}")
    if report.partial_failure:
        print(f"Completed with {len(report.errors)} error(s); see summary.txt.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
