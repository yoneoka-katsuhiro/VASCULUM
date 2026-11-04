#!/usr/bin/env python3
"""Run offline checks in isolated processes to avoid package-name collisions."""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("herbarium_specimen_collector", "checklist")


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required.")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    tests = []
    for project in PROJECTS:
        for path in sorted((ROOT / project / "scripts").rglob("*.py")):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        tests.extend(sorted((ROOT / project / "tests").glob("test_*.py")))
    for test in tests:
        subprocess.run([sys.executable, str(test)], cwd=ROOT, env=env, check=True)
    for project, script, args in (
        (PROJECTS[0], "collect_specimens.py", ["--taxon", "Haplopteris mediosora"]),
        (PROJECTS[1], "checklist.py", ["--country", "Japan", "--taxon", "Ferns"]),
    ):
        command = [sys.executable, str(ROOT / project / "scripts" / script)]
        subprocess.run(command + ["--help"], env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(command + ["--dry-run", "--contact-email", "test@example.org", *args], env=env, check=True)
    pattern = re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}")
    try:
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    except (FileNotFoundError, subprocess.CalledProcessError):
        tracked = []
    for name in tracked:
        path = ROOT / name
        if path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".sh", ".ps1"} and path.is_file():
            if pattern.search(path.read_text(encoding="utf-8")):
                raise SystemExit(f"Possible credential in {name}")
    print(f"Release checks passed: {len(tests)} test programs, compilation, CLI help and dry-runs.")


if __name__ == "__main__":
    main()
