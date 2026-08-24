param([switch]$SkipBrowser)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo

$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Python([string[]]$Arguments) {
    & $script:pythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Ensure-PythonEnvironment {
    $script:pythonPath = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $script:pythonPath)) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            throw 'Python 3.10+ was not found on PATH.'
        }
        Write-Host 'Creating .venv ...'
        & $python.Source -m venv (Join-Path $repo '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv.' }
    }

    & $script:pythonPath -c 'import alembic, fastapi, uvicorn' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing Python dependencies ...'
        Invoke-Python @('-m', 'pip', 'install', '-r', (Join-Path $repo 'requirements.txt'))
    }
}

function Ensure-FrontendBuild {
    $entry = Join-Path $repo 'ui-redesign\dist\index.html'
    if (Test-Path -LiteralPath $entry) { return }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw 'ui-redesign/dist is missing and npm was not found. Install Node.js 20+ and retry.' }
    Push-Location (Join-Path $repo 'ui-redesign')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & $npm.Source ci
            if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
        }
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Failed to build ui-redesign.' }
    } finally {
        Pop-Location
    }
}

function Test-Alive {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/health/live' -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

Ensure-PythonEnvironment
Ensure-FrontendBuild

if (Test-Alive) {
    Write-Host 'Paper-Study is already running: http://localhost:5173/workspace/'
    if (-not $SkipBrowser) { Start-Process 'http://localhost:5173/workspace/' }
    exit 0
}

$runtimeDir = Join-Path $repo 'data\local-runtime'
$null = New-Item -ItemType Directory -Force -Path $runtimeDir
$stdout = Join-Path $runtimeDir 'server.log'
$stderr = Join-Path $runtimeDir 'server.err.log'
$pidPath = Join-Path $runtimeDir 'server.pid'
Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue

$arguments = @(
    '-B', '-m', 'backend.app.cli.local_runtime',
    '--root', $repo,
    '--host', '127.0.0.1',
    '--port', '5173'
)
$process = Start-Process -FilePath $script:pythonPath -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$startedAt = try {
    ([DateTimeOffset]$process.StartTime).ToUniversalTime()
} catch {
    [DateTimeOffset]::UtcNow
}
$record = [ordered]@{
    schemaVersion = 1
    pid = $process.Id
    executable = [IO.Path]::GetFullPath($script:pythonPath)
    startedAt = $startedAt.ToString('o')
}
$record | ConvertTo-Json -Compress | Set-Content -LiteralPath $pidPath -Encoding utf8

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) { break }
    if (Test-Alive) { $ready = $true; break }
}
if (-not $ready) {
    $details = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw -LiteralPath $stderr } else { '' }
    if ($process.HasExited) { $details += "`nprocess exited with code $($process.ExitCode)" }
    throw "FastAPI failed to start. Log: $stderr`n$details"
}

Write-Host 'Started: http://localhost:5173/workspace/'
if (-not $SkipBrowser) { Start-Process 'http://localhost:5173/workspace/' }
