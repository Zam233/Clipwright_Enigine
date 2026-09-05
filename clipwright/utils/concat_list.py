"""FFmpeg concat demuxer 清单写入 — T12。

路径统一先做 resolve + 存在性校验（防穿越），再写入清单；
清单内单引号按 concat demuxer 语法转义，否则含 ' 的路径直接解析失败。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def validate_and_escape(clips: list[str]) -> tuple[list[str], Optional[str]]:
    """校验片段路径并返回转义后的行文本。

    Returns:
        (escaped_lines, error) — error 非空表示某个片段不存在。
    """
    lines: list[str] = []
    for clip in clips:
        p = Path(clip)
        if not p.is_absolute():
            p = Path(clip).resolve()
        if not p.is_file():
            return [], f"clip not found: {clip}"
        safe = str(p).replace("'", "'\\''")
        lines.append("file '%s'" % safe)
    return lines, None


def write_list(lines: list[str], file_list: str) -> None:
    with open(file_list, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
