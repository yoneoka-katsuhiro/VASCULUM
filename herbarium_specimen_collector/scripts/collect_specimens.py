#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from specimen_collector.pipeline import load_env_file, run_pipeline


PROJECT_DIR = Path(__file__).resolve().parents[1]


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VASCULUM: collect public herbarium specimen metadata and images."
    )
    parser.add_argument("--taxon", help="Accepted scientific name.")
    parser.add_argument(
        "--synonym",
        action="append",
        default=[],
        help="Additional synonym or historical name. Repeat as needed.",
    )
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated source codes. Default: all configured sources.",
    )
    parser.add_argument(
        "--contact-email",
        default="",
        help="Contact address used in the HTTP User-Agent. Defaults to CONTACT_EMAIL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory. Default: output/<taxon_name>.",
    )
    parser.add_argument(
        "--limit",
        "--max-records-per-name",
        dest="limit",
        type=int,
        default=None,
        help="Maximum records per source and search name.",
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Write DwC metadata without downloading images.",
    )
    parser.add_argument(
        "--image-resolution",
        choices=("standard", "low"),
        default="standard",
        help=(
            "Downloaded image profile. standard: max 2400 px (default); "
            "low: max 1600 px with label-readable JPEG quality."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate arguments, dependencies, and configuration without network access.",
    )
    parser.add_argument(
        "--gbif-occurrence-mode",
        choices=("specimens", "observations", "specimens-and-observations", "all-images"),
        default=None,
        help="GBIF occurrence scope. Default: specimens.",
    )
    parser.add_argument(
        "--gbif-coordinate-filter",
        choices=("any", "with-coordinates", "without-coordinates"),
        default=None,
        help="GBIF coordinate filter. Default: any.",
    )
    return parser.parse_args()


def contact_email(args: argparse.Namespace) -> str:
    load_env_file(PROJECT_DIR / ".env")
    value = (args.contact_email or os.environ.get("CONTACT_EMAIL", "")).strip()
    if not value and sys.stdin.isatty():
        value = input("Contact email for database requests: ").strip()
    if "@" not in value:
        raise ValueError(
            "A valid contact email is required. Use --contact-email or CONTACT_EMAIL."
        )
    return value


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        print("ERROR: --limit must be greater than 0.", file=sys.stderr)
        return 2

    names = [args.taxon, *args.synonym] if args.taxon else None
    try:
        report = run_pipeline(
            project_dir=PROJECT_DIR,
            contact_email=contact_email(args),
            requested_sources=split_csv(args.sources) or None,
            taxon_names=names,
            max_records_per_name=args.limit,
            skip_images=args.skip_images,
            image_resolution=args.image_resolution,
            dry_run=args.dry_run,
            output_dir=args.output,
            gbif_occurrence_mode=args.gbif_occurrence_mode,
            gbif_coordinate_filter=args.gbif_coordinate_filter,
        )
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"Configuration validated for {len(report.sources)} selected source(s). "
            "No files were written."
        )
        return 0

    print(f"Output: {report.output_dir}")
    print(f"DwC CSV: {report.output_dir / 'dwc.csv'}")
    print(f"DwC TSV: {report.output_dir / 'dwc.tsv'}")
    print(f"Images: {report.output_dir / 'images'}")
    print(f"Summary: {report.output_dir / 'summary.txt'}")
    if report.partial_failure:
        print(
            f"Completed with {len(report.errors)} error(s); see summary.txt.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
