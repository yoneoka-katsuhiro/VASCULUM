from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .inputs import first_present
from .habitat import parse_habitats
from .models import CoordinateCandidate, LabelRead


DEFAULT_LLM_PROVIDER = "codex-cli"
DEFAULT_MODEL_AUTO = "auto"
DEFAULT_REASONING_EFFORT = "xhigh"
REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max", "ultra"}
WEB_SEARCH_MODES = {"live", "cached", "indexed", "disabled"}
LLM_PROVIDERS = {"openai", "codex-cli", "custom-cli", "opus5", "fable5"}
PROVIDER_ALIASES = {"codex": "codex-cli", "chatgpt": "codex-cli", "opus": "opus5"}
MODEL_CANDIDATES_BY_PROVIDER = {
    "codex-cli": ("gpt-5.6-sol", "gpt-5.5"),
    "openai": ("gpt-5.6-sol", "gpt-5.5"),
    "opus5": ("opus5",),
    "fable5": ("fable5",),
    "custom-cli": ("auto",),
}
MODEL_CANDIDATE_ENVS = {
    "codex-cli": ("CODEX_MODEL_CANDIDATES", "LLM_MODEL_CANDIDATES"),
    "openai": ("OPENAI_MODEL_CANDIDATES", "LLM_MODEL_CANDIDATES"),
    "opus5": ("OPUS_MODEL_CANDIDATES", "LLM_MODEL_CANDIDATES"),
    "fable5": ("FABLE_MODEL_CANDIDATES", "LLM_MODEL_CANDIDATES"),
    "custom-cli": ("LLM_MODEL_CANDIDATES",),
}
MODEL_ENVS = {
    "codex-cli": ("CODEX_MODEL", "LLM_MODEL"),
    "openai": ("OPENAI_MODEL", "LLM_MODEL"),
    "opus5": ("OPUS_MODEL", "LLM_MODEL"),
    "fable5": ("FABLE_MODEL", "LLM_MODEL"),
    "custom-cli": ("LLM_MODEL",),
}


SYSTEM_PROMPT = """You are the research agent in a scientific herbarium
georeferencing pipeline. Return JSON only.

Read the attached whole specimen image directly. Identify the original
collection label and distinguish it from annotation, determination, barcode,
accession, exchange, and later herbarium labels. Never combine locality
evidence from different collecting events. Transcribe only legible text.

Then perform evidence-based georeferencing, not merely coordinate conversion.
Search the web across official gazetteers, local-language sources, historical
maps and names, protected-area records, roads, trails, collector itineraries,
topography, elevation, hydrology, and vegetation when relevant. Search in the
language of the country as well as English, and resolve historical
romanizations and former place names.

A label or DwC coordinate recorded only to degrees or whole arcminutes is an
exploration anchor, not a final georeference. Do not present its decimal
conversion as a refined coordinate. Use locality names, route context,
elevation, terrain, habitat, repeated collecting events, and independent
sources to estimate a more specific WGS84 point. Return refined coordinates
with exactly six decimal places, but report honest point-radius uncertainty in
meters; decimal formatting does not imply metre-level accuracy.

When the user supplies a taxon habitat prior, treat it as an ecological
constraint as well as a ranking signal. Check candidate points against
auditable elevation/topography, land-cover, vegetation, hydrology, coastline,
and geological-substrate sources as relevant. Prefer official national map
services; use globally applicable sources such as ESA WorldCover and
Copernicus land-cover products when local authoritative data are unavailable.
Reject obvious contradictions,
such as a subalpine forest species placed in a low-elevation built-up city or
a marine species placed inland. Absence of mapped habitat data is unknown, not
proof of absence. Habitat evidence may reject or rank geographically supported
candidates, but must never be used alone to invent an exact point.

If the locality is only a country, state, province, or similarly broad region,
return status "insufficient_locality" and no refined coordinate. If a label is
unreadable, return "label_unreadable" or "partial" and do not invent text.
"""


GEOREFERENCE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string"},
        "detectedLanguages": {"type": "array", "items": {"type": "string"}},
        "labelTranscription": {"type": "string"},
        "localityText": {"type": "string"},
        "eventDateText": {"type": "string"},
        "collectorText": {"type": "string"},
        "elevationText": {"type": "string"},
        "localityMentions": {"type": "array", "items": {"type": "string"}},
        "coordinateCandidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "latitude": {"type": "string"},
                    "longitude": {"type": "string"},
                    "geodeticDatum": {"type": "string"},
                    "uncertaintyMeters": {"type": "string"},
                    "elevationMeters": {"type": "string"},
                    "candidateType": {"type": "string"},
                    "modernPlaceName": {"type": "string"},
                    "historicalPlaceName": {"type": "string"},
                    "matchLanguage": {"type": "string"},
                    "sourceUrls": {"type": "array", "items": {"type": "string"}},
                    "evidenceLayers": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                    "score": {"type": "string"},
                    "remarks": {"type": "string"},
                },
                "required": [
                    "latitude",
                    "longitude",
                    "geodeticDatum",
                    "uncertaintyMeters",
                    "elevationMeters",
                    "candidateType",
                    "modernPlaceName",
                    "historicalPlaceName",
                    "matchLanguage",
                    "sourceUrls",
                    "evidenceLayers",
                    "evidence",
                    "score",
                    "remarks",
                ],
            },
        },
        "remarks": {"type": "string"},
    },
    "required": [
        "status",
        "detectedLanguages",
        "labelTranscription",
        "localityText",
        "eventDateText",
        "collectorText",
        "elevationText",
        "localityMentions",
        "coordinateCandidates",
        "remarks",
    ],
}


@dataclass
class LlmSettings:
    mode: str = "auto"
    provider: str = DEFAULT_LLM_PROVIDER
    model: str = DEFAULT_MODEL_AUTO
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    api_key_env: str = "OPENAI_API_KEY"
    command: str = ""
    timeout_seconds: int = 180
    web_search_mode: str = "live"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    @property
    def enabled(self) -> bool:
        if self.mode == "off":
            return False
        if self.mode == "on":
            return True
        if self.provider == "openai":
            return bool(self.api_key)
        command = resolve_cli_command(self)
        return bool(command) and (self.provider != "codex-cli" or command_available(command))


def normalize_provider(provider: str) -> str:
    key = (provider or "").strip().lower()
    return PROVIDER_ALIASES.get(key, key)


def env_first(names) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def split_candidates(value: str) -> List[str]:
    return [piece.strip() for piece in re.split(r"[,|;]", value or "") if piece.strip()]


def configured_model_request(provider: str, explicit_model: str) -> str:
    requested = (explicit_model or "").strip()
    if requested and requested.lower() != DEFAULT_MODEL_AUTO:
        return requested
    return env_first(MODEL_ENVS.get(provider, ("LLM_MODEL",))) or DEFAULT_MODEL_AUTO


def model_candidates_for(settings: LlmSettings) -> List[str]:
    requested = (settings.model or "").strip()
    if requested and requested.lower() != DEFAULT_MODEL_AUTO:
        return [requested]
    for env_name in MODEL_CANDIDATE_ENVS.get(settings.provider, ("LLM_MODEL_CANDIDATES",)):
        candidates = split_candidates(os.environ.get(env_name, ""))
        if candidates:
            return candidates
    return list(MODEL_CANDIDATES_BY_PROVIDER.get(settings.provider, ("auto",)))


def selected_model_label(settings: LlmSettings) -> str:
    candidates = model_candidates_for(settings)
    return candidates[0] if candidates else DEFAULT_MODEL_AUTO


def validate_llm_settings(settings: LlmSettings) -> None:
    settings.provider = normalize_provider(settings.provider)
    settings.model = (settings.model or "").strip() or DEFAULT_MODEL_AUTO
    if settings.mode not in {"auto", "on", "off"}:
        raise ValueError("--llm-mode must be one of: auto, on, off")
    if settings.reasoning_effort not in REASONING_EFFORTS:
        allowed = ", ".join(sorted(REASONING_EFFORTS))
        raise ValueError(f"--llm-reasoning-effort must be one of: {allowed}")
    if settings.web_search_mode not in WEB_SEARCH_MODES:
        allowed = ", ".join(sorted(WEB_SEARCH_MODES))
        raise ValueError(f"--llm-web-search must be one of: {allowed}")
    if settings.provider not in LLM_PROVIDERS:
        raise ValueError("--llm-provider must be one of: codex-cli, codex, openai, opus, opus5, fable5, custom-cli")
    if settings.provider == "openai" and settings.mode == "on" and not settings.api_key:
        raise ValueError(f"LLM mode is on, but {settings.api_key_env} is not set.")
    if settings.provider != "openai" and settings.mode == "on":
        command = resolve_cli_command(settings)
        if not command or (settings.provider == "codex-cli" and not command_available(command)):
            raise ValueError(f"LLM provider '{settings.provider}' needs an executable. Use --llm-command or put it on PATH.")


def preflight_llm(
    settings: LlmSettings,
    *,
    confirm: bool,
    external_geospatial_services: bool = False,
) -> None:
    if settings.mode == "off":
        return
    if not settings.enabled:
        if settings.mode == "on":
            raise RuntimeError(f"LLM provider '{settings.provider}' is not available.")
        return

    model_label = selected_model_label(settings)
    print(
        f"LLM preflight: provider={settings.provider}, model={model_label}, "
        f"web_search={settings.web_search_mode}",
        flush=True,
    )
    if settings.provider == "codex-cli":
        command = resolve_cli_command(settings)
        if not command or not command_available(command):
            raise RuntimeError(
                "Codex CLI was not found. Install ChatGPT/Codex CLI or set --llm-command."
            )
        print(f"LLM preflight: Codex CLI found at {command}", flush=True)
        status = subprocess.run(
            [command, "login", "status"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        combined = (status.stdout + "\n" + status.stderr).strip()
        if status.returncode != 0 or "logged in" not in combined.lower():
            raise RuntimeError(
                "Codex CLI is installed, but it does not appear to be logged in. "
                "Run `codex login` and then `codex login status`."
            )
        print("LLM preflight: Codex login OK", flush=True)
    elif settings.provider == "openai":
        if not settings.api_key:
            raise RuntimeError(
                f"OpenAI provider needs {settings.api_key_env}. Set it or use --llm-provider codex-cli."
            )
        print(f"LLM preflight: {settings.api_key_env} is set", flush=True)
    else:
        command = resolve_cli_command(settings)
        if not command:
            raise RuntimeError(
                f"Provider '{settings.provider}' needs a local command. "
                "Use --llm-command, for example: --llm-command 'opus5 --model {model} --json'."
            )
        print(f"LLM preflight: local command found for {settings.provider}", flush=True)

    if confirm:
        geospatial_notice = ""
        if external_geospatial_services:
            geospatial_notice = (
                "After LLM research, source-backed anchor coordinates and nearby "
                "mapped route, land-use, vegetation, water, coast, and substrate "
                "features may be requested from public Overpass; candidate coordinates "
                "may be sent to Open-Meteo for DEM refinement. Images and label "
                "transcriptions are not sent to those two services.\n"
            )
        print(
            "This run will call an LLM and may consume tokens/credits.\n"
            f"Provider: {settings.provider}\n"
            f"Model: {model_label}\n"
            f"Reasoning effort: {settings.reasoning_effort}\n"
            f"Web search: {settings.web_search_mode}\n"
            f"{geospatial_notice}"
            "Continue? [y/N]: ",
            end="",
            flush=True,
        )
        answer = input().strip().lower()
        if answer not in {"y", "yes"}:
            raise RuntimeError("LLM/geospatial run was cancelled by the user.")


def build_user_prompt(
    row,
    label: LabelRead,
    taxon_habitat: str,
    *,
    prompt_profile: str = "xie-modified",
    use_trails: bool = True,
    use_hydrology: bool = True,
    use_dem: bool = True,
    use_vegetation_prior: bool = True,
) -> str:
    habitat_preference = parse_habitats(taxon_habitat)
    payload = {
        "catalogNumber": row.get("catalogNumber", ""),
        "occurrenceID": row.get("occurrenceID", ""),
        "scientificName": row.get("scientificName", ""),
        "country": row.get("country", ""),
        "stateProvince": row.get("stateProvince", ""),
        "county": row.get("county", ""),
        "municipality": row.get("municipality", ""),
        "locality": row.get("locality", ""),
        "verbatimLocality": row.get("verbatimLocality", ""),
        "eventDate": row.get("eventDate", ""),
        "recordedBy": row.get("recordedBy", ""),
        "recordNumber": row.get("recordNumber", ""),
        "verbatimElevation": row.get("verbatimElevation", ""),
        "originalDecimalLatitude": row.get("decimalLatitude", ""),
        "originalDecimalLongitude": row.get("decimalLongitude", ""),
        "imagePath": label.image_path,
        "imageQualityStatus": label.image_quality_status,
        "imageQualityRemarks": label.image_quality_remarks,
        "labelStatus": label.label_status,
        "labelTranscription": label.label_transcription,
        "detectedLanguages": label.detected_languages,
        "taxonHabitatPrior": habitat_preference.as_prompt_payload(),
        "promptProfile": prompt_profile,
        "enabledEvidence": {
            "trailsAndRoads": use_trails,
            "hydrology": use_hydrology,
            "demAndElevation": use_dem,
            "vegetationAndHabitat": use_vegetation_prior,
        },
    }
    return (
        "Georeference this herbarium specimen from DwC and whole-specimen image evidence.\n"
        "Identify the main original collection label before transcribing. Ignore annotation, "
        "determination, barcode, accession, exchange, and later herbarium labels as locality "
        "sources unless they clearly repeat the original collecting event. Do not infer text "
        "that is not legible.\n\n"
        "After transcription, conduct multilingual web research. Search current and historical "
        "place names in the country's own language and English. Cross-check official or otherwise "
        "auditable gazetteers, maps, protected-area sources, roads or trails, collector routes, "
        "and elevation/topography. When a habitat prior is supplied, verify each candidate against "
        "relevant official topographic, land-use, land-cover, vegetation, hydrology, coastline, or "
        "geological maps. Use national sources first and ESA WorldCover or Copernicus land-cover "
        "products as global fallbacks. Reject clear ecological contradictions and explain the "
        "habitat evidence layer. Treat "
        "missing map coverage as unknown rather than unsuitable. When possible, compare at least two "
        "independent sources and record their URLs.\n\n"
        "Important: a coordinate printed only to degrees or whole arcminutes defines a coarse "
        "search area. It is not an acceptable final coordinate for this task. Do not return its "
        "decimal conversion as a refined result. A refined candidate must be independently placed "
        "using the locality and geographic evidence. Return WGS84 latitude and longitude with "
        "exactly six decimal places and a realistic uncertainty radius. If refinement is not "
        "defensible, return no refined candidate rather than adding false precision.\n\n"
        "Return JSON with keys: status, detectedLanguages, labelTranscription, localityText, "
        "eventDateText, collectorText, elevationText, localityMentions, coordinateCandidates, "
        "remarks. For each coordinate candidate, candidateType must be one of "
        "'refined_georeference', 'verbatim_coordinate', or 'place_centroid'. Only "
        "'refined_georeference' is eligible for primary automatic selection. When an exact "
        "collection point cannot be defended, also return the best specific, web-supported "
        "'place_centroid' or trail/road anchor as a reviewable fallback point estimate. "
        "Include sourceUrls and evidenceLayers for audit. Coordinates must be defensible.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


class OpenAIResponsesClient:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def create_json(self, user_prompt: str, image_paths: Optional[List[Path]] = None):
        user_content = user_prompt
        image_parts = openai_image_parts(image_paths or [])
        if image_parts:
            user_content = [{"type": "input_text", "text": user_prompt}] + image_parts
        errors = []
        for index, model in enumerate(model_candidates_for(self.settings)):
            payload = {
                "model": model,
                "input": [
                    {"role": "developer", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "reasoning": {"effort": self.settings.reasoning_effort},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "vasculum_georeference",
                        "schema": GEOREFERENCE_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
            }
            if self.settings.web_search_mode != "disabled":
                payload["tools"] = [{"type": "web_search"}]
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=data,
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                message = f"OpenAI API error {exc.code} with model {model}: {body}"
                errors.append(message)
                if index + 1 < len(model_candidates_for(self.settings)) and is_model_selection_error(body):
                    continue
                raise RuntimeError(message)
            except urllib.error.URLError as exc:
                raise RuntimeError(f"OpenAI API request failed: {exc}")
            parsed = parse_json_object(extract_response_text(json.loads(raw)))
            parsed["_vasculum_model"] = model
            return parsed
        raise RuntimeError("; ".join(errors) or "OpenAI API request failed.")


class CliJsonClient:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def create_json(self, user_prompt: str, image_paths: Optional[List[Path]] = None):
        command = resolve_cli_command(self.settings)
        if not command:
            raise RuntimeError(f"LLM provider '{self.settings.provider}' executable was not found.")
        rendered_prompt = prompt_with_image_paths(user_prompt, image_paths or [])
        errors = []
        for index, model in enumerate(model_candidates_for(self.settings)):
            prompt_file = ""
            input_text = rendered_prompt
            if "{prompt_file}" in command:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
                    handle.write(rendered_prompt)
                    prompt_file = handle.name
                input_text = None
            args = render_cli_command(command, model=model, prompt_file=prompt_file, image_paths=image_paths or [])
            env = os.environ.copy()
            env.setdefault("LLM_MODEL", model)
            env.setdefault("OPUS_MODEL", model)
            try:
                completed = subprocess.run(
                    args,
                    input=input_text,
                    text=True,
                    capture_output=True,
                    timeout=self.settings.timeout_seconds,
                    check=False,
                    env=env,
                )
            finally:
                if prompt_file:
                    Path(prompt_file).unlink(missing_ok=True)
            if completed.returncode != 0:
                message = f"{self.settings.provider} command failed with model {model}: {completed.stderr.strip()}"
                errors.append(message)
                if index + 1 < len(model_candidates_for(self.settings)) and is_model_selection_error(completed.stderr):
                    continue
                raise RuntimeError(message)
            parsed = parse_json_object(completed.stdout)
            parsed["_vasculum_model"] = model
            return parsed
        raise RuntimeError("; ".join(errors) or f"{self.settings.provider} command failed.")


class CodexCliClient:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def create_json(self, user_prompt: str, image_paths: Optional[List[Path]] = None):
        command = resolve_cli_command(self.settings)
        if not command:
            raise RuntimeError("codex executable was not found.")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as output:
            output_path = output.name
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".schema.json", delete=False) as schema:
            json.dump(GEOREFERENCE_RESPONSE_SCHEMA, schema)
            schema_path = schema.name
        try:
            errors = []
            candidates = model_candidates_for(self.settings)
            for index, model in enumerate(candidates):
                Path(output_path).write_text("", encoding="utf-8")
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
                for image_path in image_paths or []:
                    args.extend(["-i", str(image_path)])
                if model and model.lower() != DEFAULT_MODEL_AUTO:
                    args.extend(["-m", model])
                args.extend(
                    [
                        "-c",
                        f'model_reasoning_effort="{self.settings.reasoning_effort}"',
                        "-c",
                        f'web_search="{self.settings.web_search_mode}"',
                        "--output-schema",
                        schema_path,
                        "-o",
                        output_path,
                        "-",
                    ]
                )
                completed = subprocess.run(
                    args,
                    input=(
                        user_prompt
                        + "\n\nReturn only the JSON object. Do not edit files. "
                        "Do not run shell commands, OCR commands, image conversion, "
                        "or image cropping. Use the attached whole image directly. "
                        "Use hosted web search for the requested multilingual georeferencing research."
                    ),
                    text=True,
                    capture_output=True,
                    timeout=self.settings.timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    message = f"codex exec failed with model {model}: {completed.stderr.strip()}"
                    errors.append(message)
                    if index + 1 < len(candidates) and is_model_selection_error(completed.stderr):
                        continue
                    raise RuntimeError(message)
                parsed = parse_json_object(Path(output_path).read_text(encoding="utf-8") or completed.stdout)
                parsed["_vasculum_model"] = model
                return parsed
            raise RuntimeError("; ".join(errors) or "codex exec failed.")
        finally:
            Path(output_path).unlink(missing_ok=True)
            Path(schema_path).unlink(missing_ok=True)


def resolve_cli_command(settings: LlmSettings) -> str:
    if settings.command:
        return settings.command
    if settings.provider == "codex-cli":
        return shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"
    if settings.provider == "opus5":
        return shutil.which("opus5") or shutil.which("opus") or ""
    if settings.provider == "fable5":
        return shutil.which("fable5") or ""
    return ""


def command_available(command: str) -> bool:
    if Path(command).exists():
        return True
    return shutil.which(command) is not None


def make_client(settings: LlmSettings):
    if not settings.enabled:
        return None
    if settings.provider == "openai":
        return OpenAIResponsesClient(settings) if settings.api_key else None
    if settings.provider == "codex-cli":
        command = resolve_cli_command(settings)
        return CodexCliClient(settings) if command and command_available(command) else None
    return CliJsonClient(settings) if resolve_cli_command(settings) else None


def render_cli_command(command: str, model: str, prompt_file: str, image_paths: List[Path]):
    image_text = " ".join(shlex.quote(str(path)) for path in image_paths)
    rendered = (
        command.replace("{model}", shlex.quote(model))
        .replace("{prompt_file}", shlex.quote(prompt_file))
        .replace("{image_paths}", image_text)
    )
    return shlex.split(rendered)


def prompt_with_image_paths(user_prompt: str, image_paths: List[Path]) -> str:
    if not image_paths:
        return user_prompt
    return (
        user_prompt
        + "\n\nLocal whole specimen image file(s) available for this record:\n"
        + "\n".join(str(path) for path in image_paths)
        + "\nUse them only if your command can read local image files."
    )


def openai_image_parts(image_paths: List[Path]):
    parts = []
    for path in image_paths:
        if not path.exists():
            continue
        media_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append({"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}"})
    return parts


def extract_response_text(response_json) -> str:
    output_text = response_json.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    texts = []
    output = response_json.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                value = part.get("text") or part.get("content")
                if isinstance(value, str):
                    texts.append(value)
    return "\n".join(texts)


def parse_json_object(text: str):
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if match:
        stripped = match.group(0)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object.")
    return parsed


def candidate_base(label: LabelRead):
    return {
        "catalog_number": label.catalog_number,
        "image_path": label.image_path,
        "image_width": label.image_width,
        "image_height": label.image_height,
        "image_file_size_bytes": label.image_file_size_bytes,
        "image_quality_status": label.image_quality_status,
        "image_quality_remarks": label.image_quality_remarks,
        "detected_languages": " | ".join(label.detected_languages),
        "label_source": label.label_source,
        "label_status": label.label_status,
        "label_transcription": label.label_transcription,
        "locality_text": label.locality_text,
        "event_date_text": label.event_date_text,
        "collector_text": label.collector_text,
        "elevation_text": label.elevation_text,
    }


def llm_response_to_candidates(response, label: LabelRead, model: str):
    raw_candidates = response.get("coordinateCandidates", [])
    if not isinstance(raw_candidates, list):
        return []
    base = candidate_base(label)
    candidates = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        latitude = normalize_coordinate(first_present(raw, "latitude", "decimalLatitude"), latitude=True)
        longitude = normalize_coordinate(first_present(raw, "longitude", "decimalLongitude"), latitude=False)
        if latitude is None or longitude is None:
            continue
        candidate_type = first_present(raw, "candidateType", "type").lower()
        if not candidate_type:
            candidate_type = infer_candidate_type(raw)
        candidates.append(
            CoordinateCandidate(
                **base,
                candidate_latitude=latitude,
                candidate_longitude=longitude,
                candidate_geodetic_datum=first_present(raw, "geodeticDatum") or "WGS84",
                candidate_uncertainty_meters=first_present(raw, "uncertaintyMeters", "coordinateUncertaintyInMeters"),
                candidate_elevation_meters=first_present(raw, "elevationMeters"),
                candidate_type=candidate_type,
                modern_place_name=first_present(raw, "modernPlaceName", "placeName"),
                historical_place_name=first_present(raw, "historicalPlaceName"),
                match_language=first_present(raw, "matchLanguage", "language"),
                source_urls=join_response_list(raw.get("sourceUrls")),
                evidence_layers=join_response_list(raw.get("evidenceLayers")),
                evidence=first_present(raw, "evidence"),
                score=first_present(raw, "score"),
                candidate_source=f"llm:{model}",
                remarks=first_present(raw, "remarks") or str(response.get("remarks", "")),
            )
        )
    return candidates


def normalize_coordinate(value: str, *, latitude: bool):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    lower, upper = (-90.0, 90.0) if latitude else (-180.0, 180.0)
    if number < lower or number > upper:
        return None
    return f"{number:.6f}"


def join_response_list(value) -> str:
    if isinstance(value, list):
        return " | ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def infer_candidate_type(raw) -> str:
    text = " ".join(
        str(raw.get(name, ""))
        for name in ("evidence", "remarks")
    ).lower()
    if "direct conversion" in text or "explicit coordinate" in text or "label coordinate" in text:
        return "verbatim_coordinate"
    return "refined_georeference"


def apply_llm_label_fields(label: LabelRead, response) -> None:
    transcription = response_text(response, "labelTranscription", "transcription")
    locality = response_text(response, "localityText", "locality")
    event_date = response_text(response, "eventDateText", "eventDate")
    collector = response_text(response, "collectorText", "recordedBy", "collector")
    elevation = response_text(response, "elevationText", "verbatimElevation")
    languages = response_languages(response.get("detectedLanguages"))
    updated = False
    if transcription:
        label.label_transcription = transcription
        updated = True
    if locality:
        label.locality_text = locality
        updated = True
    if event_date:
        label.event_date_text = event_date
        updated = True
    if collector:
        label.collector_text = collector
        updated = True
    if elevation:
        label.elevation_text = elevation
        updated = True
    if languages:
        label.detected_languages = languages
        updated = True
    if updated:
        label.label_source = "llm"
        label.label_status = "llm_image_transcribed" if label.image_path else "llm_text_augmented"


def response_text(response, *names) -> str:
    for name in names:
        value = response.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            text = " | ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
        if text:
            return text
    return ""


def response_languages(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [piece.strip() for piece in re.split(r"[|,;]", value) if piece.strip()]
    return []


def is_model_selection_error(text: str) -> bool:
    lowered = (text or "").lower()
    model_terms = ("model", "モデル")
    availability_terms = (
        "not found",
        "unknown",
        "invalid",
        "unsupported",
        "unavailable",
        "does not exist",
        "not available",
        "permission",
        "access",
    )
    return any(term in lowered for term in model_terms) and any(
        term in lowered for term in availability_terms
    )
