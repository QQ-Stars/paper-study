# Paper-Study 一键关闭脚本（Windows PowerShell）
# 用法：双击 stop.cmd，或 powershell -NoProfile -ExecutionPolicy Bypass -File stop.ps1
#
# 逻辑：
#   1. 5173 在监听 -> 先优雅执行 native_runtime stop（最多等 20 秒）
#   2. 兜底：强制结束仍残留的本项目 .venv Python 进程（后端四角色）
#   3. 5180 在监听 -> 结束 ui-redesign 开发服务器（node）
#   4. 汇报两个端口的最终状态

$ErrorActionPreference = 'Continue'
$repo = $PSScriptRoot
Set-Location $repo

function Test-PortListen([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

$py = Join-Path $repo '.venv\Scripts\python.exe'
$ownerMarker = Join-Path $repo 'data\compatibility\runtime\production-owner.json'
$nativeSpec  = Join-Path $repo 'data\compatibility\runtime\native-runtime-v1.json'
$stateDir    = Join-Path $repo 'data\compatibility\runtime\native-state'

# ── 1) 优雅停止后端 ──
if (Test-PortListen 5173) {
    Write-Host '正在优雅停止后端（native_runtime stop）…'
    try {
        $owner = Get-Content -Raw -Encoding UTF8 -LiteralPath $ownerMarker | ConvertFrom-Json
        & $py -B -m backend.app.cli.native_runtime stop `
            --native-runtime-spec $nativeSpec `
            --build-identity-manifest ([string]$owner.buildIdentityManifestPath) `
            --state-directory $stateDir `
            --owner-marker $ownerMarker 2>&1 | Out-Null
    } catch {
        Write-Host ('优雅停止失败：' + $_.Exception.Message + '，将使用强制结束。')
    }
    for ($i = 0; $i -lt 10; $i++) {
        if (-not (Test-PortListen 5173)) { break }
        Start-Sleep -Seconds 2
    }
} else {
    Write-Host '后端（5173）未在运行。'
}

# ── 2) 兜底：强制结束仍残留的本项目 .venv Python 进程 ──
$venvPrefix = (Join-Path $repo '.venv') + '\'
$leftover = Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($venvPrefix, [System.StringComparison]::OrdinalIgnoreCase) }
if ($leftover) {
    Write-Host ('强制结束残留后端进程 ' + $leftover.Count + ' 个…')
    $leftover | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# ── 3) 结束 ui-redesign 开发服务器（5180） ──
$devConns = Get-NetTCPConnection -LocalPort 5180 -State Listen -ErrorAction SilentlyContinue
if ($devConns) {
    $devPids = $devConns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $devPids) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Write-Host '已停止 ui-redesign 开发服务器（5180）。'
} else {
    Write-Host 'ui-redesign 开发服务器（5180）未在运行。'
}

# ── 4) 结果汇报 ──
Start-Sleep -Seconds 1
if (Test-PortListen 5173) {
    Write-Host '警告：5173 仍被占用，请手动检查（任务管理器结束相关 python 进程）。'
} else {
    Write-Host '5173（FastAPI 后端）已释放。'
}
if (Test-PortListen 5180) {
    Write-Host '警告：5180 仍被占用，请手动检查（任务管理器结束相关 node 进程）。'
} else {
    Write-Host '5180（ui-redesign 开发服务器）已释放。'
}
Write-Host '完成。'

