#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from vasculum_checklist.models import SearchArea, TaxonFilter
from vasculum_checklist.pipeline import run_checklist
from vasculum_checklist.runtime import load_env_file


PROJECT_DIR = Path(__file__).resolve().parents[1]


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a VASCULUM CheckList species list from a geographic search."
    )
    parser.add_argument("--country", default="", help="Country name or ISO 3166-1 alpha-2 code.")
    parser.add_argument("--state", default="", help="State, province, or equivalent region.")
    parser.add_argument("--prefecture", default="", help="Prefecture name; treated like a state/province filter.")
    parser.add_argument("--latitude", type=float, default=None, help="Search center latitude.")
    parser.add_argument("--longitude", type=float, default=None, help="Search center longitude.")
    parser.add_argument("--radius", type=float, default=None, help="Search radius in km.")
    parser.add_argument(
        "--taxon",
        default="",
        help=(
            "Major group or taxon name, including family, genus, or species. "
            "Major groups: Angiosperms, Gymnosperms, Lycophytes, Ferns, Bryophytes."
        ),
    )
    parser.add_argument("--title", default="", help="Checklist title for output files.")
    parser.add_argument(
        "--sources",
        default="gbif,cvh",
        help=(
            "Comma-separated sources. GBIF is regional evidence; "
            "CVH is representative-image candidate top-up only."
        ),
    )
    parser.add_argument(
        "--contact-email",
        default="",
        help="Contact address used in the HTTP User-Agent. Defaults to CONTACT_EMAIL.",
    )
    parser.add_argument("--output", type=Path, help="Output directory.")
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum GBIF occurrence records to scan. Increase for broad searches.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=300,
        help="GBIF occurrence page size. Maximum sent to GBIF is 300.",
    )
    parser.add_argument(
        "--gbif-image-limit",
        type=int,
        default=300,
        help="Maximum GBIF image occurrences checked per species for representative images.",
    )
    parser.add_argument(
        "--image-radius-max",
        type=float,
        default=500,
        help=(
            "Preferred distance in km from the search center for GBIF representative-image bulk search. "
            "This is a ranking/search scope, not a rejection threshold."
        ),
    )
    parser.add_argument(
        "--cvh-image-limit",
        type=int,
        default=10,
        help="Maximum CVH records checked per species to top up representative-image candidates to ten.",
    )
    parser.add_argument(
        "--max-pdf-images",
        type=int,
        default=0,
        help="Maximum representative images to download for the PDF. Default: all available images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate arguments and configuration without network access or output files.",
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


def raw_command() -> str:
    return " ".join(shlex.quote(part) for part in sys.argv)


def main() -> int:
    args = parse_args()
    area = SearchArea(
        country=args.country.strip(),
        state=args.state.strip(),
        prefecture=args.prefecture.strip(),
        latitude=args.latitude,
        longitude=args.longitude,
        radius_km=args.radius,
    )
    taxon_filter = TaxonFilter(
        taxon=args.taxon.strip(),
    )
    try:
        report = run_checklist(
            project_dir=PROJECT_DIR,
            contact_email=contact_email(args),
            area=area,
            taxon_filter=taxon_filter,
            requested_sources=split_csv(args.sources),
            title=args.title.strip(),
            output_dir=args.output,
            raw_command=raw_command(),
            max_gbif_records=args.limit,
            page_size=args.page_size,
            gbif_image_limit=args.gbif_image_limit,
            image_radius_max=args.image_radius_max,
            cvh_image_limit=args.cvh_image_limit,
            max_pdf_images=args.max_pdf_images,
            dry_run=args.dry_run,
        )
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Configuration validated. No files were written.")
        return 0

    print(f"Output: {report.output_dir}")
    print(f"Species CSV: {report.output_dir / 'species_list.csv'}")
    print(f"Species TSV: {report.output_dir / 'species_list.tsv'}")
    print(f"Species Markdown: {report.output_dir / 'species_list.md'}")
    print(f"Evidence records: {report.output_dir / 'records.csv'}")
    print(f"PDF: {report.output_dir / 'checklist.pdf'}")
    print(f"Summary: {report.output_dir / 'summary.txt'}")
    if report.partial_failure:
        print(
            f"Completed with {len(report.errors)} warning/error(s); see summary.txt.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
