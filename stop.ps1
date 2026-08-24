$ErrorActionPreference = 'Continue'
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo

$runtimeDir = Join-Path $repo 'data\local-runtime'
$pidPath = Join-Path $runtimeDir 'server.pid'
$serverPid = 0
$record = $null
if (Test-Path -LiteralPath $pidPath) {
    try {
        $rawRecord = Get-Content -Raw -LiteralPath $pidPath
        $record = $rawRecord | ConvertFrom-Json
        $serverPid = [int]$record.pid
    } catch {
        Write-Host 'Warning: unsupported service state format; no process was stopped.'
    }
}

if ($serverPid -gt 0) {
    $process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
    if ($process) {
        $expectedExecutable = [IO.Path]::GetFullPath((Join-Path $repo '.venv\Scripts\python.exe'))
        $sameExecutable = $process.Path -and
            [string]::Equals($process.Path, $expectedExecutable, [StringComparison]::OrdinalIgnoreCase)
        $sameStart = $false
        if ($sameExecutable -and $record.startedAt) {
            try {
                $actualStart = ([DateTimeOffset]$process.StartTime).ToUniversalTime()
                $expectedStart = [DateTimeOffset]::Parse($record.startedAt).ToUniversalTime()
                $sameStart = [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -le 30
            } catch {
                $sameStart = $false
            }
        }
        if ($sameExecutable -and $sameStart) {
            Write-Host "Stopping FastAPI (PID $serverPid)..."
            Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000)
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "Warning: state file does not match the FastAPI process (PID $serverPid); process was not stopped."
        }
    } else {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    }
}

$listener = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host 'Warning: port 5173 is still in use; inspect the owning process.'
} else {
    Write-Host 'FastAPI stopped; port 5173 is available.'
}
