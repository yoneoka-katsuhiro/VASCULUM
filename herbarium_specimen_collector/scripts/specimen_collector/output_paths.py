from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .checkpoint import checkpoint_path
from .text_utils import safe_token


def dated_output_stem(accepted_name: str, run_date: date | None = None) -> str:
    day = (run_date or date.today()).strftime("%Y%m%d")
    return f"{day}_{safe_token(accepted_name)}"


def incomplete_output_directories(
    output_root: Path,
    accepted_name: str,
) -> list[Path]:
    if not output_root.is_dir():
        return []
    taxon = re.escape(safe_token(accepted_name))
    pattern = re.compile(rf"^\d{{8}}_{taxon}(?:_\d{{2,}})?$")
    candidates = [
        path
        for path in output_root.iterdir()
        if path.is_dir()
        and pattern.fullmatch(path.name)
        and checkpoint_path(path).is_file()
    ]
    return sorted(
        candidates,
        key=lambda path: checkpoint_path(path).stat().st_mtime,
        reverse=True,
    )


def _checkpoint_signature(path: Path) -> object:
    try:
        data = json.loads(checkpoint_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("signature")


def _select_incomplete_output(
    output_root: Path,
    accepted_name: str,
    signature: dict[str, object],
) -> Path | None:
    candidates = incomplete_output_directories(output_root, accepted_name)
    matches = [
        path
        for path in candidates
        if _checkpoint_signature(path) == signature
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        paths = ", ".join(str(path) for path in matches)
        raise ValueError(
            "Multiple matching interrupted runs were found. "
            f"Choose one with --output PATH: {paths}"
        )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        paths = ", ".join(str(path) for path in candidates)
        raise ValueError(
            "Interrupted runs were found, but none uniquely match this command. "
            f"Use the original options and --resume, or choose one with --output PATH: {paths}"
        )
    return None


def _new_output_directory(
    output_root: Path,
    accepted_name: str,
    run_date: date | None,
) -> Path:
    stem = dated_output_stem(accepted_name, run_date)
    index = 1
    while True:
        name = stem if index == 1 else f"{stem}_{index:02d}"
        candidate = output_root / name
        if not candidate.exists() or checkpoint_path(candidate).is_file():
            return candidate
        index += 1


def resolve_output_directory(
    *,
    project_dir: Path,
    accepted_name: str,
    signature: dict[str, object],
    output_dir: Path | None,
    resume: bool,
    restart: bool,
    run_date: date | None = None,
) -> Path:
    if output_dir is not None:
        return output_dir.resolve()

    output_root = project_dir / "output"
    if resume or restart:
        interrupted = _select_incomplete_output(
            output_root,
            accepted_name,
            signature,
        )
        if interrupted is not None:
            return interrupted
        if resume:
            expected = output_root / dated_output_stem(accepted_name, run_date)
            raise ValueError(
                "No checkpoint was found for this taxon and command. "
                f"Expected an interrupted run under {output_root}, such as {expected}."
            )

    return _new_output_directory(output_root, accepted_name, run_date)
