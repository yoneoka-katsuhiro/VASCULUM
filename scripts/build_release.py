#!/usr/bin/env python3
"""Build native standalone CLI tools; run this on each target operating system."""
from __future__ import annotations
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect_native_conda_licenses(stage: Path) -> list[dict]:
    """Match bundled binary paths to installed Conda records and keep notices."""
    base = Path(sys.base_prefix)
    metadata_dir = base / "conda-meta"
    if not metadata_dir.is_dir():
        return []
    native_files: set[Path] = set()

    def visit(value: object) -> None:
        if isinstance(value, (tuple, list)):
            if len(value) == 3 and isinstance(value[1], str) and value[2] in ("BINARY", "EXTENSION"):
                native_files.add(Path(value[1]).resolve())
            for item in value:
                visit(item)

    for name in ("vasculum-collect", "vasculum-checklist"):
        toc = ROOT / "build" / name / name / "Analysis-00.toc"
        if not toc.is_file():
            raise RuntimeError(f"Native dependency analysis is missing: {toc}")
        visit(ast.literal_eval(toc.read_text(encoding="utf-8")))

    inventory: list[dict] = []
    destination = stage / "THIRD_PARTY_LICENSES" / "native-conda"
    for metadata_file in sorted(metadata_dir.glob("*.json")):
        record = json.loads(metadata_file.read_text(encoding="utf-8"))
        matched = sorted(path for path in record.get("files", []) if (base / path).resolve() in native_files)
        if not matched:
            continue
        cache = Path(record.get("link", {}).get("source") or (base / "pkgs" / metadata_file.stem))
        licenses = cache / "info" / "licenses"
        if not licenses.is_dir() or not any(licenses.rglob("*")):
            raise RuntimeError(f"Native dependency licence notices are missing for {metadata_file.stem}; restore its Conda package cache before distributing.")
        package_destination = destination / metadata_file.stem
        shutil.copytree(licenses, package_destination / "licenses", dirs_exist_ok=True)
        # Conda recipes retain upstream source URLs and checksums alongside
        # the exact installed binary package version/build.
        recipe = cache / "info" / "recipe"
        if recipe.is_dir():
            shutil.copytree(recipe, package_destination / "recipe", dirs_exist_ok=True)
        about = cache / "info" / "about.json"
        if about.is_file():
            shutil.copy2(about, package_destination / "about.json")
        inventory.append({
            "name": record["name"], "version": record["version"], "build": record.get("build", ""),
            "license": record.get("license", ""), "package_url": record.get("url", ""),
            "sha256": record.get("sha256", ""), "bundled_files": matched,
        })
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return inventory


def collect_licenses(stage: Path) -> None:
    """Preserve upstream notices for dependencies and the bundled interpreter."""
    destination = stage / "THIRD_PARTY_LICENSES"
    destination.mkdir(parents=True, exist_ok=True)
    notices = ["# Third-party notices", "",
               "This release bundles Python and Python packages. The following inventory describes the build environment; some packages are build tools only.",
               "Original licence, copyright and notice files are preserved under `THIRD_PARTY_LICENSES/`. Source URLs are taken from installed package metadata.", ""]
    for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        notices += [f"## {name} {distribution.version}", ""]
        expression = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "See the preserved upstream licence files."
        notices.append("Licence metadata: " + expression.splitlines()[0])
        home = distribution.metadata.get("Home-page")
        if home:
            notices.append("Project: " + home)
        notices.extend(distribution.metadata.get_all("Project-URL") or [])
        copied = 0
        for item in distribution.files or []:
            if not any(part.lower().startswith(("license", "copying", "notice")) for part in item.parts):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file():
                relative = Path(*("parent" if part == ".." else part for part in item.parts))
                target = destination / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
        notices += [f"Preserved notice files: {copied}.", ""]
    # A conda prefix's top-level LICENSE can be the distributor's EULA;
    # prefer the interpreter's stdlib licence and retain that EULA separately.
    python_license_found = False
    locations = (Path(sysconfig.get_path("stdlib")), Path(sys.base_prefix))
    for directory in locations:
        for name in ("LICENSE.txt", "LICENSE"):
            source = directory / name
            if source.is_file():
                target = "Python-LICENSE.txt" if not python_license_found else "Python-distribution-LICENSE.txt"
                shutil.copy2(source, destination / target)
                python_license_found = True
                break
    if not python_license_found:
        raise RuntimeError("Python licence not found; provide the interpreter's licence before distributing this build.")
    native_packages = collect_native_conda_licenses(stage)
    if native_packages:
        notices += ["## Native libraries from Conda", "",
                    "The following installed Conda packages supply binary files found in the PyInstaller analysis of these executables. Their notices, available source recipes and version/build metadata are preserved under `THIRD_PARTY_LICENSES/native-conda/`.", ""]
        for package in native_packages:
            notices.append(f"- {package['name']} {package['version']} ({package['build']}): {package['license']}")
        notices.append("")
    notices += ["## Python", "", "See `THIRD_PARTY_LICENSES/Python-LICENSE.txt` for the Python licence and included copyright notices.", "",
                "Third-party specimen images are not licensed by these software notices. Their individual source terms still apply.", ""]
    (stage / "THIRD_PARTY_NOTICES.md").write_text("\n".join(notices), encoding="utf-8")


def main() -> None:
    version = (ROOT / "VERSION.txt").read_text().strip()
    suffix = ".exe" if os.name == "nt" else ""
    platform_name = {"Darwin": "macOS", "Windows": "Windows"}.get(platform.system(), platform.system())
    arch = {"aarch64": "arm64", "AMD64": "x64", "x86_64": "x64"}.get(platform.machine(), platform.machine())
    name = f"VASCULUM-{version}-{platform_name}-{arch}"
    stage = ROOT / "dist" / name
    stage.mkdir(parents=True, exist_ok=True)
    projects = (("herbarium_specimen_collector", "collector", "collect_specimens.py", "vasculum-collect"),
                ("checklist", "checklist", "checklist.py", "vasculum-checklist"))
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / "build" / "pyinstaller-cache"))
    for project, resource, entry, executable in projects:
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
                   "--name", executable, "--distpath", str(stage),
                   "--workpath", str(ROOT / "build" / executable),
                   "--specpath", str(ROOT / "build"),
                   "--paths", str(ROOT / project / "scripts"),
                   "--collect-submodules", "PIL",
                   "--exclude-module", "readline",
                   "--add-data", f"{ROOT / 'VERSION.txt'}{os.pathsep}.",
                   "--add-data", f"{ROOT / project / 'config'}{os.pathsep}{resource}/config",
                   "--add-data", f"{ROOT / project / 'VERSION.txt'}{os.pathsep}{resource}"]
        if resource == "checklist":
            command += ["--hidden-import", "vasculum_checklist.codex_vision"]
        command.append(str(ROOT / project / "scripts" / entry))
        subprocess.run(command, cwd=ROOT, env=env, check=True)
        tool = str(stage / (executable + suffix))
        subprocess.run([tool, "--help"], check=True, stdout=subprocess.DEVNULL)
        args = ["--taxon", "Haplopteris mediosora"] if resource == "collector" else ["--country", "Japan"]
        subprocess.run([tool, "--dry-run", "--contact-email", "test@example.org", *args], check=True, cwd=stage)
    for filename in ("README.md", "README_ja.md", "LICENSE", "CITATIONS.md", "CITATION.cff", "CHANGELOG.md", "VERSION.txt"):
        shutil.copy2(ROOT / filename, stage / filename)
    shutil.copytree(ROOT / "docs", stage / "docs", dirs_exist_ok=True)
    shutil.copytree(ROOT / ".github" / "assets", stage / ".github" / "assets", dirs_exist_ok=True)
    collect_licenses(stage)
    packages = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = "unavailable"
        dirty = None
    (stage / "BUILD_INFO.json").write_text(json.dumps({
        "version": version, "commit": commit, "uncommitted_changes": dirty, "python": sys.version,
        "platform": platform.platform(), "architecture": arch,
        "packages": packages.splitlines(),
    }, indent=2) + "\n", encoding="utf-8")
    archive = Path(shutil.make_archive(str(ROOT / "dist" / name), "zip", ROOT / "dist", name))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built and smoke-tested: {archive}")


if __name__ == "__main__":
    main()
