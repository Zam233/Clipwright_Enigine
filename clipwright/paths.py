"""路径锚定工具 — 将相对路径绝对化到包父目录（clipwright 的父目录），避免 CWD 依赖。"""
from __future__ import annotations

from pathlib import Path


def anchor(path: str | Path) -> Path:
    """将相对路径锚定到包父目录；绝对路径原样返回。"""
    p = Path(path)
    if p.is_absolute():
        return p
    base = Path(__file__).resolve().parent.parent
    return (base / p).resolve()
