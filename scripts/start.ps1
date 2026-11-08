# 一键启动 ClipWright（开发模式）：后端 8080 + 前端 5173 +（可选）Server 8090
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#       powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -NoFrontend   # 只起后端/账号服务（服务器、CI）
param([switch]$NoFrontend)
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

# 4. ClipWright Server（可选：账号/市场服务，端口 8090）
#    目录优先级：$env:CLIPWRIGHT_SERVER_DIR > K:\Clipwright Server
#    为什么要检查 .env：app/main.py 的 lifespan 会拒绝默认占位 secret
#    ('change-me-in-production') 启动 —— 服务进程直接退出，前端看到的就是
#    /srv 代理 ECONNREFUSED（2026-10-24 实际踩过）。这里缺 .env 就生成，
#    已存在但 secret 是占位/过短就换成随机值，避免"启动了但立刻死"。
$serverDir = if ($env:CLIPWRIGHT_SERVER_DIR) { $env:CLIPWRIGHT_SERVER_DIR } else { 'K:\Clipwright Server' }
$serverPort = if ($env:CLIPWRIGHT_SERVER_PORT) { [int]$env:CLIPWRIGHT_SERVER_PORT } else { 8090 }
$serverPid = $null

# .env 必须写成 UTF-8 **无 BOM**：PS 5.1 的 `Set-Content -Encoding utf8` 会加 BOM，
# 而 pydantic-settings 在中文 Windows 上按 cp936 读 .env → BOM 让第一行 key 变成
# 乱码前缀（`锘縩CLIPWRIGHT_SERVER_JWT_SECRET`）被忽略，服务照旧读到默认占位 secret
# 然后启动失败（2026-10-24 实测踩过）。
function Write-TextFileNoBom([string]$Path, [string[]]$Lines) {
    [System.IO.File]::WriteAllLines($Path, $Lines, (New-Object System.Text.UTF8Encoding($false)))
}

if (-not (Test-Path $serverDir)) {
    Write-Host "跳过 ClipWright Server：目录不存在 ($serverDir)" -ForegroundColor Yellow
    Write-Host '  账号/市场功能将不可用（前端 /srv 代理会连不上）；如需启用请设置 $env:CLIPWRIGHT_SERVER_DIR' -ForegroundColor DarkGray
} elseif (Get-NetTCPConnection -LocalPort $serverPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Server 已在运行 ($serverPort)" -ForegroundColor Green
} else {
    $envPath = Join-Path $serverDir '.env'
    $secretLine = if (Test-Path $envPath) {
        Get-Content $envPath | Where-Object { $_ -match '^\s*CLIPWRIGHT_SERVER_JWT_SECRET\s*=' } | Select-Object -First 1
    } else { $null }
    $secretVal = if ($secretLine) { ($secretLine -split '=', 2)[1].Trim() } else { '' }
    if (-not (Test-Path $envPath)) {
        $secret = python -c "import secrets; print(secrets.token_urlsafe(48))"
        # 用字符串数组而不是 here-string：脚本里的 here-string 容易被编辑器的
        # 编码/缩进破坏（"字符串缺少终止符"），数组写法对本文件更稳。
        $lines = @(
            '# 由 scripts/start.ps1 首次启动时生成（开发用；生产请自行更换密钥与 Mongo）',
            'CLIPWRIGHT_SERVER_HOST=127.0.0.1',
            "CLIPWRIGHT_SERVER_PORT=$serverPort",
            'CLIPWRIGHT_SERVER_DEBUG=false',
            '',
            'CLIPWRIGHT_SERVER_MONGO_URI=mongodb://localhost:27017',
            'CLIPWRIGHT_SERVER_MONGO_DB_NAME=clipwright_server',
            '',
            "CLIPWRIGHT_SERVER_JWT_SECRET=$secret",
            'CLIPWRIGHT_SERVER_JWT_ALGORITHM=HS256',
            'CLIPWRIGHT_SERVER_ACCESS_TOKEN_TTL_SEC=3600',
            'CLIPWRIGHT_SERVER_REFRESH_TOKEN_TTL_SEC=2592000',
            '',
            'CLIPWRIGHT_SERVER_STORAGE_DIR=storage',
            'CLIPWRIGHT_SERVER_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080'
        )
        Write-TextFileNoBom $envPath $lines
        Write-Host "已生成 $envPath（含随机 JWT secret）" -ForegroundColor Cyan
    } elseif (-not $secretVal -or $secretVal -eq 'change-me-in-production' -or $secretVal.Length -lt 32) {
        $newSecret = python -c "import secrets; print(secrets.token_urlsafe(48))"
        $all = @(Get-Content $envPath)
        if ($secretLine) {
            $all = @($all | ForEach-Object {
                if ($_ -match '^\s*CLIPWRIGHT_SERVER_JWT_SECRET\s*=') { "CLIPWRIGHT_SERVER_JWT_SECRET=$newSecret" } else { $_ }
            })
        } else {
            $all += "CLIPWRIGHT_SERVER_JWT_SECRET=$newSecret"
        }
        Write-TextFileNoBom $envPath $all
        Write-Host '已把 .env 的 JWT secret 从占位值换成随机值（服务端拒绝默认 secret 启动）' -ForegroundColor Cyan
    }

    Write-Host "启动 ClipWright Server ($serverPort)..." -ForegroundColor Cyan
    $p = Start-Process -WindowStyle Hidden -FilePath python `
        -ArgumentList '-X', 'utf8', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$serverPort" `
        -WorkingDirectory $serverDir `
        -RedirectStandardOutput 'logs\server.log' -RedirectStandardError 'logs\server.err.log' -PassThru
    $serverPid = $p.Id
}

Write-Host ''
Write-Host '后端 health: http://localhost:8080/health' -ForegroundColor Green
if ($serverPid -or (Get-NetTCPConnection -LocalPort $serverPort -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host "账号服务 health: http://localhost:$serverPort/health" -ForegroundColor Green
}

# 4b. 等 Server 就绪（最多 30s）：进程早退（secret/Mongo/依赖问题）直接给原因
if ($serverPid) {
    $serverReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
            Write-Host 'Server 进程已退出（未就绪）。最后 12 行 stderr：' -ForegroundColor Yellow
            if (Test-Path 'logs\server.err.log') {
                Get-Content 'logs\server.err.log' -Tail 12 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            }
            Write-Host '完整日志: logs\server.log / logs\server.err.log' -ForegroundColor DarkGray
            break
        }
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$serverPort/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $serverReady = $true; break }
        } catch { # 未就绪，继续等
        }
        Start-Sleep -Seconds 1
    }
    if ($serverReady) { Write-Host "Server 已就绪 (health OK, $serverPort)" -ForegroundColor Green }
    elseif (Get-Process -Id $serverPid -ErrorAction SilentlyContinue) {
        Write-Host "警告: 30s 内 Server 未就绪（前端 /srv 代理可能不可用）" -ForegroundColor Yellow
    }
}

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

if ($NoFrontend) {
    Write-Host '已按 -NoFrontend 跳过前端。需要时手动启动：cd J:\Clipweight-Client; npm run dev' -ForegroundColor Yellow
    exit 0
}

# 6. 前端（前台，独立仓库 J:\Clipweight-Client）
Push-Location $frontend
npm run dev
Pop-Location
