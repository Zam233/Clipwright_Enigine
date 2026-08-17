# 一键启动 ClipWright（开发模式）：后端 8080 + 前端 5173 +（可选）Server 8090
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

& "$PSScriptRoot\check_env.ps1"
if ($LASTEXITCODE -ne 0) { exit 1 }

# 1. 前端依赖
if (-not (Test-Path 'web\node_modules')) {
    Write-Host '首次运行：安装前端依赖（npm ci）...' -ForegroundColor Cyan
    npm ci --prefix web
    if ($LASTEXITCODE -ne 0) { Write-Error 'npm ci 失败'; exit 1 }
}

# 2. 后端（后台，日志写 logs/）
New-Item -ItemType Directory -Force -Path logs | Out-Null
if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host '启动后端 (8080)...' -ForegroundColor Cyan
    Start-Process -WindowStyle Hidden -FilePath python -ArgumentList '-m', 'clipwright.main' `
        -RedirectStandardOutput 'logs\backend.log' -RedirectStandardError 'logs\backend.err.log'
} else { Write-Host '后端已在运行 (8080)' -ForegroundColor Green }

# 3. ClipWright Server（可选；账号/市场启用后默认拉起）
if (Test-Path 'K:\Clipwright Server') {
    if (-not (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue)) {
        Write-Host '启动 ClipWright Server (8090)...' -ForegroundColor Cyan
        Start-Process -WindowStyle Hidden -FilePath python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', '8090' `
            -WorkingDirectory 'K:\Clipwright Server'
    } else { Write-Host 'Server 已在运行 (8090)' -ForegroundColor Green }
}

Write-Host ''
Write-Host '后端 health: http://localhost:8080/health' -ForegroundColor Green
Write-Host '前端 dev:   http://localhost:5173/  （Ctrl+C 停止前端；后端用 scripts\stop.ps1 停止）' -ForegroundColor Green

# 4. 前端（前台）
npm --prefix web run dev
