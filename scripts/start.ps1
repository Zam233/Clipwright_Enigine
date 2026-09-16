# 一键启动 ClipWright（开发模式）：后端 8080 + 前端 5173 +（可选）Server 8090
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# 前端仓库已独立（方式 A）：前端位于 J:\Clipweight-Client（不再随主项目 web/ 分发）
$frontend = 'J:\Clipweight-Client'
if (-not (Test-Path "$frontend\package.json")) {
    Write-Host "前端仓库不存在: $frontend（请确认前端已独立放置）" -ForegroundColor Red
    exit 1
}

# 1. 环境体检（含 ffmpeg / 模型缓存 / 端口 / 启动前安全红线）
& "$PSScriptRoot\check_env.ps1"
if ($LASTEXITCODE -ne 0) { exit 1 }

# 2. 前端依赖
if (-not (Test-Path "$frontend\node_modules")) {
    Write-Host '首次运行：安装前端依赖（npm ci）...' -ForegroundColor Cyan
    Push-Location $frontend
    if ($env:NPM_CACHE) { npm ci --cache $env:NPM_CACHE } else { npm ci }
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Write-Error 'npm ci 失败'; exit 1 }
}

# 3. 后端（后台，日志写 logs/）
New-Item -ItemType Directory -Force -Path logs | Out-Null
$backendPid = $null
if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host '启动后端 (8080)...' -ForegroundColor Cyan
    # `-X utf8` 不能省：这里 stdout/stderr 被**重定向到文件**，Python 会按控制台 locale
    # （中文 Windows = cp936）决定编码，日志里的中文要么乱码、要么直接抛 UnicodeEncodeError。
    # 实测：不加 → `stdout encoding = gbk` 且中文写成 `¼��ʧ��`；加 `-X utf8` → utf-8，正常。
    # （子进程插件宿主出于同一原因也会强制 -X utf8，见 plugins/runtime/host.py）
    $p = Start-Process -WindowStyle Hidden -FilePath python `
        -ArgumentList '-X', 'utf8', '-m', 'clipwright.main' `
        -RedirectStandardOutput 'logs\backend.log' -RedirectStandardError 'logs\backend.err.log' -PassThru
    $backendPid = $p.Id
} else { Write-Host '后端已在运行 (8080)' -ForegroundColor Green }

# 4. ClipWright Server（可选；账号/市场启用后默认拉起）
if (Test-Path 'K:\Clipwright Server') {
    if (-not (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue)) {
        Write-Host '启动 ClipWright Server (8090)...' -ForegroundColor Cyan
        Start-Process -WindowStyle Hidden -FilePath python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', '8090' `
            -WorkingDirectory 'K:\Clipwright Server'
    } else { Write-Host 'Server 已在运行 (8090)' -ForegroundColor Green }
}

Write-Host ''
Write-Host '后端 health: http://localhost:8080/health' -ForegroundColor Green

# 5. 等待后端就绪（最多 60s），避免前端先起导致 /api 代理 ECONNREFUSED
$backendReady = $false
for ($i = 0; $i -lt 60; $i++) {
    # 进程早退（例如安全红线拒绝、端口/依赖异常）→ 立刻报错退出，别让用户对着连不上的前端猜
    if ($backendPid -and -not (Get-Process -Id $backendPid -ErrorAction SilentlyContinue)) {
        Write-Host '后端进程已退出（未就绪）。最后 15 行 stderr：' -ForegroundColor Red
        if (Test-Path 'logs\backend.err.log') {
            Get-Content 'logs\backend.err.log' -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        }
        Write-Host '完整日志: logs\backend.log / logs\backend.err.log' -ForegroundColor Yellow
        exit 1
    }
    try {
        # 5s 超时：health 含 chromadb 检查，偶尔 >2s，2s 会误报未就绪
        $resp = Invoke-WebRequest -Uri 'http://localhost:8080/health' -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $backendReady = $true; break }
    } catch { # 未就绪，继续等待
    }
    Start-Sleep -Seconds 1
}
if ($backendReady) {
    Write-Host '后端已就绪 (health OK)' -ForegroundColor Green
} else {
    # 注意：开了 CLIPWRIGHT_API_TOKEN 时 /health 可能返回 401，这里按"进程还活着"处理
    Write-Host '警告: 60s 内后端未就绪，继续启动前端（/api 代理可能短暂不可用）' -ForegroundColor Yellow
    Write-Host '  排查：logs\backend.err.log；或直接访问 http://localhost:8080/health' -ForegroundColor DarkGray
}

Write-Host '前端 dev:   http://localhost:5173/  （Ctrl+C 停止前端；后端用 scripts\stop.ps1 停止）' -ForegroundColor Green

# 6. 前端（前台，独立仓库 J:\Clipweight-Client）
Push-Location $frontend
npm run dev
Pop-Location
