"""命令分级与硬底线（PRD F11 / 开发计划 B9）。

分工（PRD §F11 的「就高不就低」）：

- **硬底线**：读凭据 / 不可逆系统破坏 —— 由确定性规则判定，不经 LLM、不可授权；
- **危险种子**：删/移/覆盖既有文件、装包、起服务、改权限、网络下载后执行 ——
  只用于把 LLM 判定的等级**往上抬**，永不往下调；
- 其余命令的最终等级由独立 LLM（`supervisor/review.assess`）给出。
"""

from __future__ import annotations

import re

#: 硬底线一：读取凭据（不可授权，任何模式都拒绝）
CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|[\s\"'=])\.env(\s|$|\"|')", re.IGNORECASE),
    re.compile(r"\.ssh[\\/]", re.IGNORECASE),
    re.compile(r"id_rsa|id_ed25519", re.IGNORECASE),
    re.compile(r"keychain|secret[_-]?store", re.IGNORECASE),
)

#: 硬底线二：不可逆系统破坏（不可授权）
DESTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f?\s+/(\s|$)", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b", re.IGNORECASE),
    re.compile(r"reg\s+(delete|add)\s+.*HKLM", re.IGNORECASE),
    re.compile(r"\bmkfs\b|\bdiskpart\b|\bbcdedit\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b.*-Recurse.*\bC:\\\\", re.IGNORECASE),
)

#: 危险种子：命中则至少 med（只升不降）
DANGER_SEEDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(rm|del|erase|Remove-Item|rmdir)\b", re.IGNORECASE),
    re.compile(r"\b(mv|move|Move-Item|copy|cp)\b", re.IGNORECASE),
    re.compile(r"\b(pip|npm|apt|apt-get|choco|winget)\s+install\b", re.IGNORECASE),
    re.compile(
        r"\b(start|uvicorn|node|python\s+-m\s+http)\b.*(serve|server|--port)", re.IGNORECASE,
    ),
    re.compile(r"\b(chmod|chown|icacls|attrib)\b", re.IGNORECASE),
    re.compile(r"(curl|wget|Invoke-WebRequest)\b.*\|\s*(bash|sh|python)", re.IGNORECASE),
    re.compile(r">\s*[^\s|&]+", re.IGNORECASE),  # 输出重定向覆盖文件
)

_ORDER = ("low", "med", "high", "critical")


def hard_floor(command: str) -> str:
    """命中硬底线则返回原因，否则空串。"""
    cmd = str(command or "")
    for pat in CREDENTIAL_PATTERNS:
        if pat.search(cmd):
            return f"读取凭据（{pat.pattern}）"
    for pat in DESTRUCTION_PATTERNS:
        if pat.search(cmd):
            return f"不可逆系统破坏（{pat.pattern}）"
    return ""


def ratchet_level(command: str) -> str:
    """按危险种子给出「最低风险等级」（命中则 med，否则 low）。"""
    cmd = str(command or "")
    return "med" if any(p.search(cmd) for p in DANGER_SEEDS) else "low"


def max_level(a: str, b: str) -> str:
    """取更高的风险等级（只升不降）。"""
    ai = _ORDER.index(a) if a in _ORDER else 0
    bi = _ORDER.index(b) if b in _ORDER else 0
    return _ORDER[max(ai, bi)]
