# Reproducible builds / 配布版の作成

VASCULUM distributes two terminal executables: `vasculum-collect` and `vasculum-checklist`. Windows names end in `.exe`. The executables include Python and configuration resources; Codex CLI and system fonts remain external.

## Native build

Build on the intended operating system and architecture. PyInstaller does not produce a Windows executable from a macOS build.

```bash
python3 -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements-build.txt -c constraints-build.txt
.venv-build/bin/python scripts/check_release.py
.venv-build/bin/python scripts/build_release.py
```

On Windows, use `py -3.13 -m venv .venv-build` and `.venv-build\Scripts\python.exe` for the subsequent commands. The supported source minimum is Python 3.11; release builds use Python 3.13. Version constraints record the tested Python dependencies, including platform-specific build dependencies.

The builder creates a ZIP and matching `.zip.sha256` under `dist/`. Each archive contains both executables, bilingual documentation, the existing example gallery, licence/citation files, `THIRD_PARTY_NOTICES.md`, preserved upstream notices under `THIRD_PARTY_LICENSES/`, and `BUILD_INFO.json`. That manifest records the source commit, Python version, platform, architecture and installed packages. For a release, build from a clean commit and keep the CI job log.

Both frozen programs are started with `--help` and an offline `--dry-run` before the archive is produced. A failed smoke check aborts the build. Local app sandboxes can prevent the onefile loader from creating OS semaphores; a `semctl` permission error is a host restriction, not a passed smoke check. Use the native GitHub Actions runner to validate that artifact.

## CI and publication

`.github/workflows/tests.yml` runs on pushes, pull requests and manual dispatch. It:

1. Tests the minimum Python version on Ubuntu.
2. Runs the isolated offline suites on macOS 26 and Windows Server 2022 with Python 3.13. A local macOS 15 arm64 build is tested separately.
3. Builds native executables and smoke-tests their packaged configuration.
4. Uploads ZIP files and SHA-256 files as Actions artifacts.

The workflow has read-only repository permissions and **does not publish releases**. A maintainer reviews the successful platform jobs and artifacts, tags the tested commit, and attaches those artifacts to a release. Do not label a platform as validated if its job failed or was never run. Apple notarization, a Developer ID signature and Windows publisher signing require separate maintainer credentials and are not configured here.

## 日本語での要点

対象OS上でビルドします。macOSからWindows版を生成する仕組みではありません。`scripts/check_release.py`は、collectorとchecklistを別プロセスで検証し、同名Pythonパッケージの混入を防ぎます。`scripts/build_release.py`は2個の実行ファイルを作り、ヘルプと通信なしの設定検証に成功した場合だけZIPを作成します。

配布時は、変更を記録したコミットに対してCIを実行し、macOS・Windows両方のジョブと成果物を確認してください。`BUILD_INFO.json`とチェックサムを残すことで、どのコードと環境から作った配布物かを確認できます。署名・公証は別工程です。
