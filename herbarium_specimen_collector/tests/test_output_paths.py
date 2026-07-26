from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from specimen_collector.checkpoint import checkpoint_path
from specimen_collector.output_paths import (
    dated_output_stem,
    incomplete_output_directories,
    resolve_output_directory,
)


def write_test_checkpoint(output_dir: Path, signature: dict[str, object]) -> None:
    path = checkpoint_path(output_dir)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"signature": signature}, separators=(",", ":")),
        encoding="utf-8",
    )


def run_test() -> None:
    run_date = date(2026, 7, 26)
    signature = {"accepted_name": "Haplopteris mediosora", "version": "v0.1.4"}
    assert (
        dated_output_stem("Haplopteris mediosora", run_date)
        == "20260726_Haplopteris_mediosora"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        output_root = project_dir / "output"
        base = output_root / "20260726_Haplopteris_mediosora"

        fresh = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=None,
            resume=False,
            restart=False,
            run_date=run_date,
        )
        assert fresh == base

        base.mkdir(parents=True)
        second = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=None,
            resume=False,
            restart=False,
            run_date=run_date,
        )
        assert second == output_root / "20260726_Haplopteris_mediosora_02"
        second.mkdir()
        third = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=None,
            resume=False,
            restart=False,
            run_date=run_date,
        )
        assert third == output_root / "20260726_Haplopteris_mediosora_03"

        interrupted = output_root / "20260725_Haplopteris_mediosora"
        write_test_checkpoint(interrupted, signature)
        resumed = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=None,
            resume=True,
            restart=False,
            run_date=run_date,
        )
        assert resumed == interrupted
        assert incomplete_output_directories(
            output_root,
            "Haplopteris mediosora",
        ) == [interrupted]

        same_day_interrupted = output_root / "20260726_Haplopteris_mediosora_03"
        write_test_checkpoint(same_day_interrupted, {"different": True})
        selected_for_new_run = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=None,
            resume=False,
            restart=False,
            run_date=run_date,
        )
        assert selected_for_new_run == same_day_interrupted

        duplicate_match = output_root / "20260724_Haplopteris_mediosora"
        write_test_checkpoint(duplicate_match, signature)
        try:
            resolve_output_directory(
                project_dir=project_dir,
                accepted_name="Haplopteris mediosora",
                signature=signature,
                output_dir=None,
                resume=True,
                restart=False,
                run_date=run_date,
            )
        except ValueError as exc:
            assert "Multiple matching interrupted runs" in str(exc)
            assert "--output PATH" in str(exc)
        else:
            raise AssertionError("Multiple matching checkpoints were accepted.")

        explicit = project_dir / "custom"
        resolved_explicit = resolve_output_directory(
            project_dir=project_dir,
            accepted_name="Haplopteris mediosora",
            signature=signature,
            output_dir=explicit,
            resume=False,
            restart=False,
            run_date=run_date,
        )
        assert resolved_explicit == explicit.resolve()


if __name__ == "__main__":
    run_test()
    print("Output path tests passed.")
