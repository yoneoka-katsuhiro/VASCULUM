from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from georeference_curator.llm import (
    CodexCliClient,
    LlmSettings,
    OpenAIResponsesClient,
    build_transcription_prompt,
    build_user_prompt,
    cache_prompt,
    codex_event_result,
    normalize_token_usage,
    response_schema,
    validate_llm_settings,
)
from georeference_curator.llm_cache import LlmResponseCache
from georeference_curator.models import LabelRead
from georeference_curator.parallel import RateLimitBackoff
from georeference_curator.pipeline import create_llm_json_with_backoff, run_pipeline
from georeference_curator.progress import TerminalProgress


from test_offline import write_label_photos


class TokenEfficiencyTests(unittest.TestCase):
    def test_stage_contracts(self):
        transcription = LlmSettings(task_mode="transcription_only", web_search_mode="disabled")
        research = LlmSettings()
        self.assertIn("labelTranscription", response_schema(transcription)["properties"])
        self.assertNotIn("coordinateCandidates", response_schema(transcription)["properties"])
        self.assertEqual(set(response_schema(research)["properties"]), {"status", "coordinateCandidates", "remarks"})
        prompt = build_user_prompt({"country": "Japan"}, LabelRead())
        self.assertIn("Do not repeat or revise the transcription", prompt)
        self.assertNotEqual(cache_prompt(transcription, prompt), cache_prompt(research, prompt))

    def test_request_identity_preserves_geographic_context(self):
        row = {"catalogNumber": "A", "country": "Japan", "eventDate": "1901"}
        label = LabelRead(label_transcription="Mt. Example trail, 2000 m")
        prompt = build_user_prompt(row, label)
        self.assertEqual(prompt, build_user_prompt(dict(row, catalogNumber="B"), label))
        for changed in (dict(row, country="China"), dict(row, eventDate="2001"), dict(row, decimalLatitude="35.1")):
            self.assertNotEqual(prompt, build_user_prompt(changed, label))
        self.assertEqual(prompt, build_user_prompt(dict(row, scientificName="Example species"), label))
        self.assertNotEqual(prompt, build_user_prompt(dict(row, habitat="Recorded forest"), label))
        self.assertEqual(
            build_transcription_prompt(row, label),
            build_transcription_prompt(dict(row, catalogNumber="B"), label),
        )

    def test_usage_formats(self):
        self.assertEqual(normalize_token_usage(None), {})
        self.assertEqual(normalize_token_usage({"input_tokens": -1}), {})
        self.assertEqual(
            normalize_token_usage({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 60}, "output_tokens": 10}),
            {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10},
        )
        text, usage = codex_event_result('\n'.join((
            "non-json diagnostic",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"status":"ok"}'}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 90, "cached_input_tokens": 40, "output_tokens": 9}}),
        )))
        self.assertEqual(text, '{"status":"ok"}')
        self.assertEqual(usage["cached_input_tokens"], 40)

    def test_supported_reasoning(self):
        for model in ("gpt-6.1-sol", "gpt-6-astra"):
            validate_llm_settings(LlmSettings(mode="off", model=model, reasoning_effort="medium"))
            with self.assertRaises(ValueError):
                validate_llm_settings(LlmSettings(mode="off", model=model, reasoning_effort="none"))

    def test_codex_schema_model_and_usage(self):
        settings = LlmSettings(model="gpt-6.1-sol", task_mode="transcription_only", web_search_mode="disabled")

        def fake_run(args, **kwargs):
            self.assertIn("--json", args)
            self.assertNotIn("--ignore-rules", args)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
            self.assertEqual(args[args.index("-m") + 1], "gpt-6.1-sol")
            self.assertIn('model_reasoning_effort="medium"', args)
            schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
            self.assertNotIn("coordinateCandidates", schema["properties"])
            self.assertIn("Keep different collecting events separate", kwargs["input"])
            Path(args[args.index("-o") + 1]).write_text('{"status":"ok","labelTranscription":"Mount Example"}')
            return subprocess.CompletedProcess(args, 0, json.dumps({"type": "turn.completed", "usage": {"input_tokens": 99, "output_tokens": 12}}), "")

        with patch("georeference_curator.llm.subprocess.run", side_effect=fake_run):
            response = CodexCliClient(settings).create_json("Read this label")
        self.assertEqual(response["_vasculum_usage"]["output_tokens"], 12)

    def test_responses_api_stage_contract(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({"status": "completed", "output_text": '{"status":"ok","coordinateCandidates":[],"remarks":""}', "usage": {"input_tokens": 50, "output_tokens": 5}}).encode()

        def fake_open(request, **_kwargs):
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "gpt-6.1-sol")
            self.assertNotIn("labelTranscription", payload["text"]["format"]["schema"]["properties"])
            return Response()

        with patch("georeference_curator.llm.urllib.request.urlopen", side_effect=fake_open):
            result = OpenAIResponsesClient(LlmSettings(model="gpt-6.1-sol")).create_json("Locate")
        self.assertEqual(result["_vasculum_usage"]["input_tokens"], 50)

    def test_parallel_duplicates_and_content_cache(self):
        class Client:
            calls = 0

            def create_json(self, _prompt, image_paths=None):
                self.calls += 1
                time.sleep(0.02)
                return {"status": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_a, image_b = root / "a.jpg", root / "b.jpg"
            image_a.write_bytes(b"same-whole-image")
            image_b.write_bytes(image_a.read_bytes())
            cache = LlmResponseCache(root / "cache")
            key_args = dict(purpose="transcription_only", provider="codex-cli", model="gpt-test", reasoning_effort="medium", web_search_mode="disabled", prompt="same")
            self.assertEqual(cache.key(**key_args, image_paths=[image_a]), cache.key(**key_args, image_paths=[image_b]))
            image_b.write_bytes(b"different-image")
            self.assertNotEqual(cache.key(**key_args, image_paths=[image_a]), cache.key(**key_args, image_paths=[image_b]))
            client = Client()
            barrier = threading.Barrier(4)

            def call(_index):
                barrier.wait(timeout=5)
                return create_llm_json_with_backoff(client, "same", [], backoff=RateLimitBackoff(), retries=0, progress=TerminalProgress(False), catalog="TEST", settings=LlmSettings(), purpose="georeference", cache=cache)

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(call, range(4)))
            self.assertEqual(client.calls, 1)
            self.assertEqual(sum(result[3] for result in results), 3)

    def test_pipeline_preserves_label_and_accounts_for_reuse(self):
        class Client:
            def __init__(self, settings):
                self.settings = settings
                self.calls = 0

            def create_json(self, _prompt, image_paths=None):
                self.calls += 1
                return {
                    "status": "ok", "coordinateCandidates": [],
                    "labelTranscription": "Unwanted research rewrite", "remarks": "",
                    "_vasculum_model": self.settings.model,
                    "_vasculum_usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10},
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            fields = ["catalogNumber", "country", "locality", "eventDate"]
            with (source / "dwc.tsv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
                writer.writeheader()
                writer.writerows(dict(catalogNumber=name, country="Japan", locality="Mt. Example trail", eventDate="1901") for name in ("A", "B"))
            labels = write_label_photos(root, [dict(catalogNumber=name, labelTranscription="Mt. Example trail", localityText="Mt. Example trail") for name in ("A", "B")], input_dir=source)
            clients = []

            def make_client(settings):
                client = Client(settings)
                clients.append(client)
                return client

            with patch.dict("os.environ", {"PATH": os.environ.get("PATH", os.defpath)}, clear=True), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", side_effect=make_client), patch("georeference_curator.pipeline.preflight_llm"):
                report = run_pipeline(
                    project_dir=root, input_dir=source, input_dwc=None, output_dir=root / "out", label_tsv=labels, gazetteer_tsv=None,
                    dry_run=False, review_distance_km=5,
                    llm_mode="on", llm_provider="codex-cli", llm_model="gpt-6.1-sol",
                    llm_reasoning_effort="", llm_web_search="live", llm_command="/bin/true", llm_api_key_env="OPENAI_API_KEY",
                    llm_rate_limit_retries=0, confirm_llm=False, georeferenced_by="test",
                    use_trails=True, use_hydrology=True, use_dem=False, use_vegetation_prior=True,
                    taxon_habitat="forest", workers="2", progress=TerminalProgress(False),
                    label_model="gpt-6.1-terra", label_reasoning_effort="low",
                )
            self.assertEqual(clients[0].calls, 1)
            self.assertEqual(clients[1].settings.model, "gpt-6.1-terra")
            self.assertEqual(clients[1].settings.reasoning_effort, "low")
            self.assertEqual(clients[1].settings.task_mode, "transcription_only")
            self.assertEqual(report.llm_reasoning_effort, "medium")
            self.assertEqual(report.llm_cache_hits, 1)
            self.assertEqual(report.llm_input_tokens, 100)
            self.assertEqual(report.llm_usage_reported_requests, 1)
            candidates = (root / "out" / "georeference_candidates.tsv").read_text()
            self.assertIn("Mt. Example trail", candidates)
            self.assertNotIn("Unwanted research rewrite", candidates)
            log = [json.loads(line) for line in (root / "out" / "georeference.log.jsonl").read_text().splitlines()]
            self.assertEqual(sum(bool(entry["llmUsage"][0]["usage"]) for entry in log), 1)


if __name__ == "__main__":
    unittest.main()
