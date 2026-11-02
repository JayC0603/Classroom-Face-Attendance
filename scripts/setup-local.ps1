param(
    [switch]$SkipInstall,
    [switch]$DownloadFaceModels
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

function New-RandomHex([int]$byteCount) {
    $bytes = New-Object byte[] $byteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

function New-UrlSafeToken([int]$byteCount) {
    $bytes = New-Object byte[] $byteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (-not (Test-Path -LiteralPath '.env')) {
    $dbPassword = New-RandomHex 24
    $minioPassword = New-RandomHex 24
    $fernetBytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($fernetBytes) } finally { $rng.Dispose() }
    $fernetKey = [Convert]::ToBase64String($fernetBytes)
    $envText = @"
# Generated for this local machine. Keep this file private and untracked.
POSTGRES_DB=face_recognition_dev
POSTGRES_USER=face_recognition_dev
POSTGRES_PASSWORD=$dbPassword
POSTGRES_PORT=55432
REDIS_PORT=56379
MINIO_PORT=59000
MINIO_CONSOLE_PORT=59001
MINIO_ROOT_USER=face-recognition-dev
MINIO_ROOT_PASSWORD=$minioPassword
FR_POSTGRES_DSN=postgresql://face_recognition_dev:$dbPassword@127.0.0.1:55432/face_recognition_dev
FR_AUTH_RATE_LIMIT_KEY=$(New-UrlSafeToken 48)
REDIS_URL=redis://127.0.0.1:56379/0
EVIDENCE_ROOT=data/evidence
FR_CAMERA_ENCRYPTION_KEY=$fernetKey
FR_WORKER_INTERNAL_TOKEN=$(New-UrlSafeToken 32)
FR_MODEL_ARTIFACT_ROOT=data/models
FR_FACE_IMAGE_ROOT=data/face-images
MINIO_ENDPOINT=127.0.0.1:59000
MINIO_ACCESS_KEY=face-recognition-dev
MINIO_SECRET_KEY=$minioPassword
MINIO_MODELS_BUCKET=models
MINIO_FACE_IMAGES_BUCKET=face-images
MINIO_EVIDENCE_BUCKET=evidence
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $root '.env'),
        $envText.TrimStart(),
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host 'Created a private local .env with generated credentials.'
} else {
    Write-Host 'Keeping the existing .env unchanged.'
}

$envValues = @{}
foreach ($line in Get-Content -LiteralPath '.env') {
    if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
        $envValues[$matches[1]] = $matches[2].Trim('"', "'")
    }
}
$requiredKeys = @(
    'FR_POSTGRES_DSN', 'FR_AUTH_RATE_LIMIT_KEY', 'FR_WORKER_INTERNAL_TOKEN',
    'FR_CAMERA_ENCRYPTION_KEY', 'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY'
)
$missing = @($requiredKeys | Where-Object { -not $envValues.ContainsKey($_) -or -not $envValues[$_] })
if ($missing.Count -gt 0) {
    throw "Missing required values in .env: $($missing -join ', '). Keep .env private and fill them before setup."
}
if ($envValues['FR_AUTH_RATE_LIMIT_KEY'] -match 'replace-with|<generate' -or
    $envValues['FR_WORKER_INTERNAL_TOKEN'] -match 'replace-with|<generate') {
    throw 'Replace the development placeholders in .env before starting the API.'
}

$hostPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $hostPython) { throw 'Python 3.11 or newer is required. Install Python and reopen PowerShell.' }
$hostPythonVersion = & $hostPython.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$hostPythonVersion -lt [version]'3.11') {
    throw "Python 3.11 or newer is required; found $hostPythonVersion."
}

$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) { throw 'Node.js and npm are required. Install Node.js and reopen PowerShell.' }
$nodeVersion = [version](& $node.Source -p "process.versions.node")
$nodeSupported = ($nodeVersion.Major -eq 20 -and $nodeVersion.Minor -ge 19) -or
    ($nodeVersion.Major -ge 22 -and ($nodeVersion.Major -gt 22 -or $nodeVersion.Minor -ge 12))
if (-not $nodeSupported) {
    throw "Vite requires Node 20.19+ or 22.12+; found $nodeVersion."
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { throw 'Docker Desktop is required. Install and start it, then reopen PowerShell.' }
& $docker.Source compose version | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable. Update Docker Desktop and try again.' }

if (-not $SkipInstall) {
    $venvPython = Join-Path $root '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $hostPython.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
    & $venvPython -m pip install -e '.[dev,cpu]'
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & $npm.Source ci --prefix apps/web
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
}

if ($DownloadFaceModels) {
    $venvPython = Join-Path $root '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) { $venvPython = $hostPython.Source }
    & $venvPython scripts/setup_face_models.py
    if ($LASTEXITCODE -ne 0) { throw 'Downloading or verifying YuNet/SFace weights failed.' }
}

Write-Host ''
Write-Host 'Local setup is ready. Run .\scripts\start-local.ps1 to launch the app.'
if (-not $DownloadFaceModels) {
    Write-Host 'For face comparison, also run .\scripts\setup-local.ps1 -SkipInstall -DownloadFaceModels.'
}
