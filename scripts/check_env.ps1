# 环境体检：python / node / mongo / ffmpeg / 模型缓存 / 端口 / 启动前安全红线
#
# 为什么能力检查（ffmpeg、模型缓存）也放在这里：这两个缺失**不会**让后端起不来，
# 只会让某几个功能在运行到一半时才失败（ffmpeg 缺失 → 建索引/渲染/抽帧全废；
# 模型没缓存 → 首次建索引现场下载几百 MB）。开工前说清楚，比事后查日志便宜。
#
# 用法: powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
$ErrorActionPreference = 'SilentlyContinue'
$script:ok = $true
$script:warn = 0
$root = Split-Path $PSScriptRoot -Parent

function Check([string]$name, [bool]$cond, [string]$hint) {
    if ($cond) { Write-Host "[OK]   $name" -ForegroundColor Green }
    else { Write-Host "[FAIL] $name — $hint" -ForegroundColor Red; $script:ok = $false }
}

function Warn([string]$msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
    $script:warn++
}

# ── 读 .env（不依赖任何 CLI，逐行解析 CLIPWRIGHT_ 前缀项）──
# 注意：settings.json（设置页落盘）优先级高于 .env，但要启动后才有意义；
# 这里只读 .env，够覆盖当前开发方式。
$script:envMap = @{}
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    Warn '.env 不存在（将使用内置默认值）——本地开发建议: Copy-Item .env.example .env'
} else {
    foreach ($raw in (Get-Content -LiteralPath $envFile -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        $line = $raw.Trim().TrimStart([char]0xFEFF)
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
        $i = $line.IndexOf('=')
        $script:envMap[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim()
    }
}

function Env-Value([string]$key, [string]$fallback) {
    if ($script:envMap.ContainsKey($key) -and $script:envMap[$key]) { return $script:envMap[$key] }
    return $fallback
}

# ── 基础运行时 ──
$py = python --version 2>&1
Check 'Python >= 3.12' ($py -match '3\.(1[2-9]|[2-9][0-9])') '安装 Python 3.12+ 并加入 PATH'
$node = node --version 2>&1
Check 'Node >= 20' ($node -match 'v(2[0-9]|[3-9][0-9])') '安装 Node 20+'
$mongo = Get-NetTCPConnection -LocalPort 27017 -State Listen -ErrorAction SilentlyContinue
Check 'MongoDB (27017)' ($null -ne $mongo) '启动 MongoDB，或运行: docker run -d -p 27017:27017 mongo:7'

# ── ffmpeg / ffprobe（渲染、抽帧、建索引、STT 全依赖；不在 PATH 时后端起得来但功能废）──
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
$fp = Get-Command ffprobe -ErrorAction SilentlyContinue
Check 'ffmpeg' ($null -ne $ff) '安装 ffmpeg 并加入 PATH（winget install Gyan.FFmpeg），或在 .env 设 CLIPWRIGHT_FFMPEG_PATH 指向 exe'
Check 'ffprobe' ($null -ne $fp) 'ffprobe 通常随 ffmpeg 一起安装（winget install Gyan.FFmpeg）'

# ── 模型缓存（缺失不是错误，是"首次会很慢/离线会失败"）──
function HF-Cache-Dir {
    if ($env:HF_HOME) { return $env:HF_HOME }
    if ($env:HUGGINGFACE_HUB_CACHE) { return $env:HUGGINGFACE_HUB_CACHE }
    return (Join-Path $env:USERPROFILE '.cache\huggingface')
}

function Test-ModelCached([string]$modelId) {
    $dir = Join-Path (Join-Path (HF-Cache-Dir) 'hub') ('models--' + ($modelId -replace '/', '--'))
    return (Test-Path $dir)
}

$embProvider = Env-Value 'CLIPWRIGHT_RAG_EMBED_PROVIDER' 'local'
$embModel    = Env-Value 'CLIPWRIGHT_RAG_EMBED_MODEL' ''
if ($embProvider -eq 'sentence_transformer' -and $embModel) {
    if (Test-ModelCached $embModel) {
        Write-Host "[OK]   嵌入模型已缓存（$embModel）" -ForegroundColor Green
    } else {
        Warn "嵌入模型未缓存（$embModel）——首次建索引会现场下载（百 MB 级）；离线环境请先手动下载"
    }
}

# 画面标签用视觉模型（CLIP / ViT）；缺缓存只是首次慢，零样本标签会退化为不可用
$visModel = Env-Value 'CLIPWRIGHT_VISION_MODEL' ''
if ($visModel) {
    if (Test-ModelCached $visModel) {
        Write-Host "[OK]   视觉模型已缓存（$visModel）" -ForegroundColor Green
    } else {
        Warn "视觉模型未缓存（$visModel）——首次跑画面理解会现场下载"
    }
}

# ── 前端仓库 + 端口 ──
if (-not (Test-Path 'J:\Clipweight-Client\package.json')) {
    Warn '前端仓库不存在（J:\Clipweight-Client）——start 将跳过前端'
}

foreach ($p in 8080, 8090, 5173) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Warn "端口 $p 已被占用（可能为已有实例，start 将跳过）" }
}

# ── 启动前安全红线（与后端 _enforce_security_gate 同一条规则）──
# 后端 fail-closed：绑定非回环地址又没有任何鉴权 → 直接拒绝启动。
# 放在这里是因为原先前端会照常起来，用户只看到一个连不上的界面。
$host_ = Env-Value 'CLIPWRIGHT_HOST' '0.0.0.0'
$token = Env-Value 'CLIPWRIGHT_API_TOKEN' ''
$verify = Env-Value 'CLIPWRIGHT_ACCOUNT_VERIFY_MODE' 'off'
$insec = (Env-Value 'CLIPWRIGHT_ALLOW_INSECURE' 'false').ToLower()
$loopback = @('127.0.0.1', 'localhost', '::1', '') -contains $host_.ToLower()
$authed = ($token -ne '') -or (@('token', 'jwt') -contains $verify.ToLower())
if ($loopback -or $authed -or $insec -eq 'true') {
    $why = '回环地址，开发模式'
    if ($authed) { $why = '已配置鉴权' } elseif ($insec -eq 'true') { $why = '显式自认风险' }
    Write-Host "[OK]   安全红线（host=$host_，$why）" -ForegroundColor Green
} else {
    Check "安全红线（host=$host_ 且无鉴权）" $false `
        '后端会拒绝启动。改 .env：CLIPWRIGHT_HOST=127.0.0.1（本机开发，推荐）或设 CLIPWRIGHT_API_TOKEN=<随机串> 或 CLIPWRIGHT_ALLOW_INSECURE=true（受控内网自认风险）'
}

Write-Host ''
if (-not $script:ok) { Write-Host '环境体检未通过' -ForegroundColor Red; exit 1 }
if ($script:warn -gt 0) { Write-Host "环境体检通过（$script:warn 条提醒，不阻断启动）" -ForegroundColor Green }
else { Write-Host '环境体检通过' -ForegroundColor Green }
