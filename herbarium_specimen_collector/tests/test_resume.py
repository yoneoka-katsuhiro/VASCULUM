from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import specimen_collector.pipeline as pipeline
from specimen_collector.checkpoint import CheckpointError, checkpoint_path
from specimen_collector.images import ImageResult
from specimen_collector.models import SpecimenRecord


def run_test() -> None:
    original_collector = pipeline.collect_source_records
    original_downloader = pipeline.download_images
    calls: list[str] = []
    interrupt_on_synonym = True
    interrupt_during_images = False
    include_images = False

    def fake_collect_source_records(**kwargs: object) -> tuple[list[SpecimenRecord], str]:
        nonlocal interrupt_on_synonym
        query_name = str(kwargs["query_name"])
        source = str(kwargs["source"])
        calls.append(query_name)
        if interrupt_on_synonym and query_name == "Test synonym":
            raise KeyboardInterrupt
        return (
            [
                SpecimenRecord(
                    source=source,
                    query_name=query_name,
                    occurrence_id=f"{source}:{query_name}",
                    institution_code="TEST",
                    catalog_number=query_name.replace(" ", "_"),
                    scientific_name=query_name,
                    basis_of_record="PRESERVED_SPECIMEN",
                    image_url=(
                        f"https://example.org/{query_name}.jpg"
                        if include_images
                        else ""
                    ),
                    download_status="pending" if include_images else "no_image_url",
                )
            ],
            "fake adapter completed",
        )

    def fake_download_images(**kwargs: object) -> ImageResult:
        nonlocal interrupt_during_images
        records = kwargs["records"]
        assert isinstance(records, list)
        if interrupt_during_images:
            interrupt_during_images = False
            raise KeyboardInterrupt
        for record in records:
            assert isinstance(record, SpecimenRecord)
            if record.image_url:
                record.download_status = "downloaded"
        return ImageResult(
            downloaded=sum(
                bool(record.image_url)
                for record in records
                if isinstance(record, SpecimenRecord)
            )
        )

    pipeline.collect_source_records = fake_collect_source_records
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "resume-output"
            common = {
                "project_dir": ROOT,
                "contact_email": "test@example.org",
                "requested_sources": ["gbif"],
                "taxon_names": ["Test taxon", "Test synonym"],
                "max_records_per_name": None,
                "skip_images": True,
                "image_resolution": "standard",
                "dry_run": False,
                "output_dir": output_dir,
            }

            try:
                pipeline.run_pipeline(**common)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("The simulated interruption did not occur.")

            state_path = checkpoint_path(output_dir)
            assert state_path.exists()
            assert calls == ["Test taxon", "Test synonym"]
            first_logs = sorted((output_dir / "logs").glob("run_*.log"))
            assert len(first_logs) == 1
            first_log_text = first_logs[0].read_text(encoding="utf-8")
            assert '"event": "run_stopped"' in first_log_text
            assert '"event": "checkpoint_saved_after_stop"' in first_log_text
            assert "test@example.org" not in first_log_text

            try:
                pipeline.run_pipeline(**common)
            except ValueError as exc:
                assert "incomplete run" in str(exc)
            else:
                raise AssertionError("A stale checkpoint was not detected.")

            mismatched = dict(common)
            mismatched["image_resolution"] = "low"
            mismatched["resume"] = True
            try:
                pipeline.run_pipeline(**mismatched)
            except CheckpointError as exc:
                assert "do not match" in str(exc)
            else:
                raise AssertionError("A mismatched checkpoint was accepted.")

            interrupt_on_synonym = False
            calls.clear()
            resumed = dict(common)
            resumed["resume"] = True
            report = pipeline.run_pipeline(**resumed)

            assert calls == ["Test synonym"]
            assert report.records_found == 2
            assert len(report.records) == 2
            assert not state_path.parent.exists()
            assert (output_dir / "dwc.csv").exists()
            assert (output_dir / "dwc.tsv").exists()
            assert (output_dir / "summary.txt").exists()
            all_logs = sorted((output_dir / "logs").glob("run_*.log"))
            assert len(all_logs) == 2
            resumed_log = all_logs[-1].read_text(encoding="utf-8")
            assert '"mode": "resume"' in resumed_log
            assert '"event": "run_completed"' in resumed_log
            assert "test@example.org" not in resumed_log
            assert "Log file: logs/" in (
                output_dir / "summary.txt"
            ).read_text(encoding="utf-8")

            restart_output = Path(tmpdir) / "restart-output"
            restart_common = dict(common)
            restart_common["output_dir"] = restart_output
            interrupt_on_synonym = True
            calls.clear()
            try:
                pipeline.run_pipeline(**restart_common)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("The restart setup was not interrupted.")
            assert checkpoint_path(restart_output).exists()

            interrupt_on_synonym = False
            calls.clear()
            restart_common["restart"] = True
            restarted_report = pipeline.run_pipeline(**restart_common)
            assert calls == ["Test taxon", "Test synonym"]
            assert restarted_report.records_found == 2
            assert not checkpoint_path(restart_output).parent.exists()

            image_output = Path(tmpdir) / "image-resume-output"
            image_common = dict(common)
            image_common["output_dir"] = image_output
            image_common["skip_images"] = False
            include_images = True
            interrupt_on_synonym = False
            interrupt_during_images = True
            pipeline.download_images = fake_download_images
            calls.clear()
            try:
                pipeline.run_pipeline(**image_common)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("The image phase was not interrupted.")
            assert calls == ["Test taxon", "Test synonym"]
            assert checkpoint_path(image_output).exists()

            calls.clear()
            image_common["resume"] = True
            image_report = pipeline.run_pipeline(**image_common)
            assert calls == []
            assert image_report.records_found == 2
            assert not checkpoint_path(image_output).parent.exists()
    finally:
        pipeline.collect_source_records = original_collector
        pipeline.download_images = original_downloader


if __name__ == "__main__":
    run_test()
    print("Resume tests passed.")
