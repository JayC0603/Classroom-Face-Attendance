$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath '.env')) { throw 'Run .\scripts\setup-local.ps1 first; .env is missing.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\scripts\setup-local.ps1 first; .venv is missing.' }
& $python -m uvicorn control_api.main:app --env-file .env --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw "Control API stopped with exit code $LASTEXITCODE." }
