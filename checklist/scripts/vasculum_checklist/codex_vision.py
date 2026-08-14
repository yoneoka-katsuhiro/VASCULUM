from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_REASONING_EFFORT = "high"
DEFAULT_WEB_SEARCH = "disabled"


SELECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selected_index": {"type": ["integer", "null"]},
        "selected_source_record_id": {"type": "string"},
        "selected_image_url": {"type": "string"},
        "selected_sha256": {"type": "string"},
        "rejected_source_record_ids": {"type": "array", "items": {"type": "string"}},
        "rejected_image_urls": {"type": "array", "items": {"type": "string"}},
        "valid_specimen_count": {"type": "integer"},
        "diagnostic_sufficiency": {"type": "string", "enum": ["sufficient", "insufficient"]},
        "candidate_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "source_record_id": {"type": "string"},
                    "image_url": {"type": "string"},
                    "valid_specimen": {"type": "boolean"},
                    "rejection_reason": {"type": "string"},
                },
                "required": [
                    "index",
                    "source_record_id",
                    "image_url",
                    "valid_specimen",
                    "rejection_reason",
                ],
            },
        },
        "reason": {"type": "string"},
    },
    "required": [
        "selected_index",
        "selected_source_record_id",
        "selected_image_url",
        "selected_sha256",
        "rejected_source_record_ids",
        "rejected_image_urls",
        "valid_specimen_count",
        "diagnostic_sufficiency",
        "candidate_assessments",
        "reason",
    ],
}


def split_candidates(value: str) -> list[str]:
    return [piece.strip() for piece in re.split(r"[,|;]", value or "") if piece.strip()]


def model_candidates() -> list[str]:
    explicit = os.environ.get("CODEX_MODEL", "").strip() or os.environ.get("LLM_MODEL", "").strip()
    if explicit:
        return [explicit]
    configured = (
        os.environ.get("CODEX_MODEL_CANDIDATES", "").strip()
        or os.environ.get("LLM_MODEL_CANDIDATES", "").strip()
    )
    return split_candidates(configured) or ["auto"]


def resolve_codex_command() -> str:
    configured = (
        os.environ.get("VASCULUM_CODEX_COMMAND", "").strip()
        or os.environ.get("CODEX_COMMAND", "").strip()
        or os.environ.get("LLM_COMMAND", "").strip()
    )
    if configured:
        return configured
    return shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"


def command_available(command: str) -> bool:
    return bool(command) and (Path(command).exists() or shutil.which(command) is not None)


def preflight() -> None:
    command = resolve_codex_command()
    if not command_available(command):
        raise RuntimeError("Codex CLI was not found. Install Codex CLI or set VASCULUM_CODEX_COMMAND.")
    status = subprocess.run(
        [command, "login", "status"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    combined = f"{status.stdout}\n{status.stderr}".strip()
    if status.returncode != 0 or "logged in" not in combined.lower():
        raise RuntimeError("Codex CLI is installed, but it does not appear to be logged in.")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(0)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise RuntimeError("Codex selector did not return a JSON object.")
    return parsed


def candidate_payload(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates):
        payload.append(
            {
                "index": str(index),
                "source": str(candidate.get("source", "")),
                "source_record_id": str(candidate.get("source_record_id", "")),
                "taxon": str(candidate.get("taxon", "")),
                "basis_of_record": str(candidate.get("basis_of_record", "")),
                "institution_code": str(candidate.get("institution_code", "")),
                "catalog_number": str(candidate.get("catalog_number", "")),
                "country": str(candidate.get("country", "")),
                "raw_country": str(candidate.get("raw_country", "")),
                "state_province": str(candidate.get("state_province", "")),
                "county": str(candidate.get("county", "")),
                "municipality": str(candidate.get("municipality", "")),
                "distance_km": str(candidate.get("distance_km", "")),
                "geographic_match": str(candidate.get("geographic_match", "")),
                "administrative_match": str(candidate.get("administrative_match", "")),
                "image_url": str(candidate.get("image_url", "")),
                "alternate_url": str(candidate.get("alternate_url", "")),
                "downloaded_url": str(candidate.get("downloaded_url", "")),
                "sha256": str(candidate.get("sha256", "")),
                "local_path": str(candidate.get("local_path", "")),
            }
        )
    return payload


def selector_prompt(species: Any, search_area: Any, candidates: list[dict[str, Any]], instruction: str) -> str:
    payload = {
        "target_species": {
            "canonical_species": getattr(species, "canonical_species", ""),
            "full_scientific_name": getattr(species, "full_scientific_name", ""),
            "family": getattr(species, "family", ""),
            "genus": getattr(species, "genus", ""),
        },
        "search_area": {
            "country": getattr(search_area, "country", ""),
            "state": getattr(search_area, "state", ""),
            "prefecture": getattr(search_area, "prefecture", ""),
            "latitude": getattr(search_area, "latitude", None),
            "longitude": getattr(search_area, "longitude", None),
            "radius_km": getattr(search_area, "radius_km", None),
        },
        "candidates": candidate_payload(candidates),
    }
    return (
        "You are selecting a representative herbarium specimen image for a VASCULUM checklist.\n"
        "Use only the attached whole images and candidate metadata. Do not edit files. "
        "Do not run commands.\n\n"
        f"{instruction}\n\n"
        "Rules:\n"
        "- The target taxon must match the candidate taxon. Reject a candidate if the image clearly represents another taxon.\n"
        "- Reject living plant photographs, field observation photographs, illustrations, label-only images, placeholders, and unavailable-image screens.\n"
        "- Prefer a herbarium sheet or other preserved herbarium specimen image that is diagnostically useful for the target species.\n"
        "- If one or more valid herbarium specimen images remain, you must select the best available specimen even if it is not ideal.\n"
        "- Return diagnostic_sufficiency as sufficient only when the selected specimen is useful enough to stop reviewing later candidates; otherwise return insufficient.\n"
        "- Return selected_index null only when every candidate is wrong taxon, living/observation photo, illustration, placeholder, broken/unusable, or non-specimen.\n"
        "- For every candidate, return candidate_assessments with valid_specimen true/false and a rejection_reason. Use an empty rejection_reason for selected/valid candidates.\n"
        "- selected_index is zero-based and must refer to the candidate list below.\n\n"
        "Candidate metadata JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def select_representative_image(
    *,
    species: Any,
    search_area: Any,
    candidates: list[dict[str, Any]],
    instruction: str,
) -> dict[str, Any]:
    command = resolve_codex_command()
    image_paths = [Path(str(candidate.get("local_path", ""))) for candidate in candidates if candidate.get("local_path")]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as output:
        output_path = Path(output.name)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".schema.json", delete=False) as schema:
        json.dump(SELECTION_RESPONSE_SCHEMA, schema)
        schema_path = Path(schema.name)
    try:
        errors: list[str] = []
        for model in model_candidates():
            output_path.write_text("", encoding="utf-8")
            args = [
                command,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
            ]
            for image_path in image_paths:
                args.extend(["-i", str(image_path)])
            if model and model.lower() != "auto":
                args.extend(["-m", model])
            args.extend(
                [
                    "-c",
                    f'model_reasoning_effort="{os.environ.get("CODEX_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)}"',
                    "-c",
                    f'web_search="{os.environ.get("CODEX_WEB_SEARCH", DEFAULT_WEB_SEARCH)}"',
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "-",
                ]
            )
            completed = subprocess.run(
                args,
                input=selector_prompt(species, search_area, candidates, instruction),
                text=True,
                capture_output=True,
                timeout=int(os.environ.get("VASCULUM_CODEX_TIMEOUT_SECONDS", "600")),
                check=False,
            )
            if completed.returncode != 0:
                errors.append(f"codex exec failed with model {model}: {completed.stderr.strip()}")
                continue
            return parse_json_object(output_path.read_text(encoding="utf-8") or completed.stdout)
        raise RuntimeError("; ".join(errors) or "codex exec failed.")
    finally:
        output_path.unlink(missing_ok=True)
        schema_path.unlink(missing_ok=True)
