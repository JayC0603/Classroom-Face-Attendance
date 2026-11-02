$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { throw 'Docker Desktop is required.' }
& $docker.Source compose --env-file .env --file infrastructure/local/compose.yaml stop
if ($LASTEXITCODE -ne 0) { throw 'Could not stop local infrastructure.' }
Write-Host 'Local services stopped. PostgreSQL, Redis, and MinIO volumes were preserved.'
