# 环境体检：python / node / mongo / 端口
$ErrorActionPreference = 'SilentlyContinue'
$script:ok = $true

function Check([string]$name, [bool]$cond, [string]$hint) {
    if ($cond) { Write-Host "[OK]   $name" -ForegroundColor Green }
    else { Write-Host "[FAIL] $name — $hint" -ForegroundColor Red; $script:ok = $false }
}

$py = python --version 2>&1
Check 'Python >= 3.12' ($py -match '3\.(1[2-9]|[2-9][0-9])') '安装 Python 3.12+ 并加入 PATH'
$node = node --version 2>&1
Check 'Node >= 20' ($node -match 'v(2[0-9]|[3-9][0-9])') '安装 Node 20+'
$mongo = Get-NetTCPConnection -LocalPort 27017 -State Listen -ErrorAction SilentlyContinue
Check 'MongoDB (27017)' ($null -ne $mongo) '启动 MongoDB，或运行: docker run -d -p 27017:27017 mongo:7'
if (-not (Test-Path '.\web\package.json')) {
    Write-Host '[WARN] web\ 目录不存在（monorepo 未合并？）' -ForegroundColor Yellow
}

foreach ($p in 8080, 8090, 5173) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Write-Host "[WARN] 端口 $p 已被占用（可能为已有实例，start 将跳过）" -ForegroundColor Yellow }
}

if (-not $script:ok) { Write-Host '环境体检未通过' -ForegroundColor Red; exit 1 }
Write-Host '环境体检通过' -ForegroundColor Green
