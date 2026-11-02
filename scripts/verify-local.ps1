$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\scripts\setup-local.ps1 first.' }

function Invoke-Check([string]$label, [scriptblock]$command) {
    Write-Host "`n== $label ==" -ForegroundColor Cyan
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE." }
}

Invoke-Check 'Python tests' { & $python -m pytest --basetemp .test-tmp -p no:cacheprovider }
Invoke-Check 'Python lint' { & $python -m ruff check . }
Invoke-Check 'Python types' { & $python -m mypy apps/control-api/src workers/vision-worker/src packages/python }
Push-Location (Join-Path $root 'apps/web')
try {
    Invoke-Check 'Web typecheck' { npm run typecheck }
    Invoke-Check 'Web tests' { npm run test -- --pool=threads --maxWorkers=1 }
    Invoke-Check 'Web production build' { npm run build }
} finally {
    Pop-Location
}
Write-Host "`nAll local verification checks passed." -ForegroundColor Green
