"""跨仓脚本的统一路径解析：优先环境变量，其次同级目录。"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def frontend_root() -> Path:
    """前端仓库根目录。

    优先级：
    1. ``CLIPWRIGHT_FRONTEND_ROOT``（绝对路径或 ``~`` 开头）；
    2. 与本仓库同级的 ``Clipweight-Client``（双仓并列 clone 的默认布局）。
    """
    raw = os.environ.get("CLIPWRIGHT_FRONTEND_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO_ROOT.parent / "Clipweight-Client").resolve()
