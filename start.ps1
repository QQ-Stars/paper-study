# Paper-Study 一键启动脚本（Windows PowerShell）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1 [-SkipBrowser]
# 或双击 start.cmd。
#
# 逻辑：
#   1. owner marker 不是 python_active -> 报错退出（需先完成一次性 P6 接管）
#   2. 5173 已在监听且 /health/live 200 -> 视为已运行，直接打开浏览器
#   3. 否则用 owner marker 中记录的精确 frozen identity 路径执行 native_runtime start

param([switch]$SkipBrowser)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
Set-Location $repo

$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$sqliteDll = 'D:\Programming\Environment\Anaconda\pkgs\sqlite-3.51.2-hee5a0db_0\Library\bin'
if (Test-Path $sqliteDll) { $env:P3_SQLITE_DLL_DIR = $sqliteDll }

$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'ERROR: .venv 不存在，请先执行 python -m venv .venv 并安装 requirements.txt'; exit 1 }

$ownerMarker = Join-Path $repo 'data\compatibility\runtime\production-owner.json'
$nativeSpec  = Join-Path $repo 'data\compatibility\runtime\native-runtime-v1.json'
$stateDir    = Join-Path $repo 'data\compatibility\runtime\native-state'

function Test-Alive {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/health/live' -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (-not (Test-Path $ownerMarker)) {
    Write-Host 'ERROR: 未找到 owner marker，尚未完成一次性 P6 接管，不能直接启动。'
    exit 1
}
$owner = Get-Content -Raw -Encoding UTF8 -LiteralPath $ownerMarker | ConvertFrom-Json
if ($owner.ownerState -ne 'python_active') {
    Write-Host ('ERROR: ownerState=' + $owner.ownerState + '（非 python_active）。请先完成受控 P6 接管，勿绕过门禁。')
    exit 1
}
$biPath = [string]$owner.buildIdentityManifestPath
if (-not (Test-Path $biPath)) { Write-Host ('ERROR: owner marker 引用的 BuildIdentity 不存在: ' + $biPath); exit 1 }

if (Test-Alive) {
    $listener = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
    Write-Host ('Paper-Study 已在运行（python_active, listener pid=' + $listener[0].OwningProcess + '）。')
} else {
    Write-Host 'Paper-Study 未运行，使用 frozen identity 启动四角色（api/worker/scheduler/mcp）...'
    & $py -B -m backend.app.cli.native_runtime start `
        --native-runtime-spec $nativeSpec `
        --build-identity-manifest $biPath `
        --state-directory $stateDir `
        --owner-marker $ownerMarker
    if ($LASTEXITCODE -ne 0) { Write-Host ('ERROR: native_runtime start 退出码 ' + $LASTEXITCODE); exit $LASTEXITCODE }
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Alive) { $ok = $true; break }
    }
    if (-not $ok) { Write-Host 'ERROR: 启动后 60 秒内 /health/live 未就绪，请查看 ' + (Join-Path $stateDir 'logs\api.log'); exit 1 }
    Write-Host '启动成功：/health/live -> 200'
}

Write-Host '打开 http://localhost:5173 （/workspace/ = React 工作区，/legacy/ = 旧版入口）'
if (-not $SkipBrowser) { Start-Process 'http://localhost:5173' }
