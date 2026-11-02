param([switch]$WithWorker)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { throw 'Docker Desktop is required. Start it and run setup-local.ps1 first.' }
if (-not (Test-Path -LiteralPath '.env')) { throw 'Run .\scripts\setup-local.ps1 first; .env is missing.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run .\scripts\setup-local.ps1 first.' }
if (-not (Test-Path -LiteralPath 'apps/web/node_modules')) { throw 'Run .\scripts\setup-local.ps1 first.' }

& $docker.Source compose --env-file .env --file infrastructure/local/compose.yaml up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL, Redis, and MinIO did not become healthy.' }

$shell = (Get-Process -Id $PID).Path
function Start-LocalTerminal([string]$scriptName) {
    $scriptPath = Join-Path $PSScriptRoot $scriptName
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}"' -f $scriptPath
    Start-Process -FilePath $shell -ArgumentList $arguments -WorkingDirectory $root | Out-Null
}

$apiHealth = $null
try { $apiHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 } catch { }
if (-not $apiHealth) {
    Start-LocalTerminal 'run-api.ps1'
    $deadline = (Get-Date).AddSeconds(120)
    do {
        Start-Sleep -Seconds 2
        try { $apiHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 } catch { }
    } while (-not $apiHealth -and (Get-Date) -lt $deadline)
}
if (-not $apiHealth) { throw 'The API did not become healthy in 120 seconds. Check its terminal window.' }

if ($WithWorker) { Start-LocalTerminal 'run-worker.ps1' }
Start-LocalTerminal 'run-web.ps1'
Start-Process 'http://127.0.0.1:5173'
Write-Host 'API: http://127.0.0.1:8000/health'
Write-Host 'Web: http://127.0.0.1:5173'
Write-Host 'MinIO console: http://127.0.0.1:59001'
Write-Host 'Close the API, worker, and web terminal windows to stop those processes.'
Write-Host 'Run .\scripts\stop-local.ps1 to stop local infrastructure while keeping its data.'
