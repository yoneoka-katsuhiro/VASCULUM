$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.13 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 from python.org, then retry.' }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
& .\.venv\Scripts\python.exe -m pip install -r herbarium_specimen_collector\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe scripts\check_release.py
if ($LASTEXITCODE -ne 0) { throw 'Offline release checks failed.' }
Write-Host 'Setup complete. See docs/MANUAL_EN.md or docs/MANUAL_JA.md.'
