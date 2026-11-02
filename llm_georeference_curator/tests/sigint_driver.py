"""No-network subprocess fixture for end-to-end SIGINT regression tests."""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from georeference_curator.interrupts import run_cli_process
from curate_georeferences import main

root = Path(sys.argv[1])
pid_file = root / "children.txt"


class Client:
    def create_json(self, *_args, **_kwargs):
        run_cli_process([sys.executable, "-c",
            "import os,time,sys; f=open(sys.argv[1],'a'); f.write(str(os.getpid())+'\\n'); f.flush(); f.close(); time.sleep(600)",
            str(pid_file)], timeout=600)
        raise AssertionError("Interrupted provider must not return a geographic result")


sys.argv = ["curate", "--input", str(root / "input"), "--output", str(root / "out"),
    "--label-tsv", str(root / "labels.tsv"), "--workers", "2", "--llm-mode", "on", "--yes",
    "--no-geospatial-refinement", "--no-verify-source-urls"]
with patch("georeference_curator.llm.resolve_cli_command", return_value="/test/bin/codex"), patch("georeference_curator.llm.command_available", return_value=True), patch("georeference_curator.pipeline.make_client", return_value=Client()), patch("georeference_curator.pipeline.preflight_llm"):
    raise SystemExit(main())
