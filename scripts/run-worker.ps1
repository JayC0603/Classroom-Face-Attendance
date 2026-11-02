$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath '.env')) { throw 'Run .\scripts\setup-local.ps1 first; .env is missing.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\scripts\setup-local.ps1 first; .venv is missing.' }
$tokenLine = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^\s*FR_WORKER_INTERNAL_TOKEN\s*=' } | Select-Object -First 1
if (-not $tokenLine) { throw 'FR_WORKER_INTERNAL_TOKEN is missing from .env.' }
$env:FR_WORKER_INTERNAL_TOKEN = ($tokenLine -replace '^\s*FR_WORKER_INTERNAL_TOKEN\s*=\s*', '').Trim('"', "'")
& $python -m vision_worker.main --control-api-url http://127.0.0.1:8000 `
    --feature-config configuration/examples/face-recognition.example.json --processing-fps 5
if ($LASTEXITCODE -ne 0) { throw "Vision worker stopped with exit code $LASTEXITCODE." }
