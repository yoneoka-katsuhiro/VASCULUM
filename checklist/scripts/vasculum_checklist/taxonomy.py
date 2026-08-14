from __future__ import annotations

import json
from pathlib import Path

from .models import SpeciesEntry, clean_text


def load_family_order(project_dir: Path) -> dict[str, int]:
    path = project_dir / "config" / "taxonomic_family_order.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    families = data.get("family_order", [])
    return {
        clean_text(family).lower(): index
        for index, family in enumerate(families)
        if clean_text(family)
    }


def family_rank(family: str, ranks: dict[str, int]) -> int:
    return ranks.get(clean_text(family).lower(), 1_000_000)


def sort_species_by_taxonomy(species: list[SpeciesEntry], family_ranks: dict[str, int]) -> list[SpeciesEntry]:
    return sorted(
        species,
        key=lambda entry: (
            family_rank(entry.family, family_ranks),
            entry.family.lower(),
            (entry.genus or entry.canonical_species.split(" ", 1)[0]).lower(),
            entry.canonical_species.lower(),
        ),
    )


def families_in_species_order(species: list[SpeciesEntry]) -> list[str]:
    families: list[str] = []
    seen: set[str] = set()
    for entry in species:
        family = entry.family or "(family unknown)"
        if family not in seen:
            seen.add(family)
            families.append(family)
    return families


def checklist_page_numbers(
    species: list[SpeciesEntry],
    first_page: int = 3,
    per_page: int = 4,
) -> dict[str, int]:
    page_by_name: dict[str, int] = {}
    page = first_page
    current_family: str | None = None
    cards_on_page = 0
    for entry in species:
        family = entry.family or "(family unknown)"
        if family != current_family:
            if current_family is not None:
                page += 1
            current_family = family
            cards_on_page = 0
        elif cards_on_page >= per_page:
            page += 1
            cards_on_page = 0
        page_by_name[entry.canonical_species] = page
        cards_on_page += 1
    return page_by_name
