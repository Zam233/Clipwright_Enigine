"""安全工具 — ID 校验与路径安全检查（防路径遍历）。"""

from __future__ import annotations

import re
from pathlib import Path

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SecurityViolation(ValueError):
    """安全校验失败（非法 ID / 路径遍历），API 层应返回 400。"""


def is_safe_id(value: str) -> bool:
    """校验 ID 是否安全：仅字母/数字/下划线/连字符/点，且以字母或数字开头。"""
    return bool(value) and bool(_ID_PATTERN.match(value))


def validate_id(value: str, name: str = "id") -> str:
    """校验 ID，非法时抛 SecurityViolation。"""
    if not is_safe_id(value):
        raise SecurityViolation(
            f"非法 {name}: {value!r}（仅允许字母、数字、下划线、连字符、点，且以字母或数字开头）"
        )
    return value


def is_within(base: Path, target: Path) -> bool:
    """判断 target 解析后是否位于 base 内。"""
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    return target_resolved == base_resolved or base_resolved in target_resolved.parents


def safe_join(base: Path, user_path: str) -> Path:
    """将用户路径拼接到基目录并校验结果仍在基目录内，否则抛 SecurityViolation。"""
    target = base / user_path
    if not is_within(base, target):
        raise SecurityViolation(f"路径超出允许目录: {user_path!r}")
    return target.resolve()
