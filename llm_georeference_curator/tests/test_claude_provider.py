from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from georeference_curator.llm import ClaudeCliClient, LlmSettings, make_client, normalize_provider, validate_llm_settings


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(%r, "w") as handle:
    json.dump({"args": args, "prompt": prompt}, handle)
print(json.dumps({"type": "result", "is_error": False, "result": "",
    "structured_output": {"status": "ok", "labelTranscription": "Alishan", "localityText": "Alishan"},
    "usage": {"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 90, "output_tokens": 40}}))
"""


class ClaudeProviderTests(unittest.TestCase):
    def test_claude_alias_and_print_mode_arguments(self):
        self.assertEqual(normalize_provider("claude"), "claude-cli")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / "call.json"
            command = root / "claude"
            command.write_text(FAKE_CLAUDE % str(record))
            command.chmod(command.stat().st_mode | stat.S_IEXEC)
            photo = root / "images" / "P02439185.jpg"
            photo.parent.mkdir()
            photo.write_bytes(b"photo")
            settings = LlmSettings(mode="on", provider="claude", command=str(command), model="opus",
                                   task_mode="transcription_only", web_search_mode="disabled")
            validate_llm_settings(settings)
            client = make_client(settings)
            self.assertIsInstance(client, ClaudeCliClient)
            response = client.create_json("Read the label.", [photo])
            call = json.loads(record.read_text())
            args = call["args"]
            self.assertEqual(response["localityText"], "Alishan")
            self.assertEqual(response["_vasculum_usage"], {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 40})
            self.assertEqual(args[args.index("--model") + 1], "opus")
            self.assertEqual(args[args.index("--tools") + 1], "Read")
            self.assertEqual(args[args.index("--add-dir") + 1], str(photo.parent))
            self.assertIn("--json-schema", args)
            self.assertIn(str(photo), call["prompt"])

    def test_research_gets_web_tools_only_when_enabled(self):
        live = ClaudeCliClient(LlmSettings(provider="claude-cli", task_mode="georeference", web_search_mode="live"))
        self.assertEqual(live.tools([]), "WebSearch,WebFetch")
        offline = ClaudeCliClient(LlmSettings(provider="claude-cli", task_mode="georeference", web_search_mode="disabled"))
        self.assertEqual(offline.tools([]), "")


if __name__ == "__main__":
    unittest.main()
