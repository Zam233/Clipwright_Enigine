"""切句策略（2026-11-03 从 `clipwright/services/voice.py` 整体搬入，B3-T6）。

为什么搬：裁定 3 要求**编排（含切句）**离开 engine，由 TTS 功能插件承担。
engine 的 `VoiceService` 只保留协议调度（clone / synthesize），不再知道"怎么切句"。

搬运纪律：函数体**逐字不变**（含正则与 docstring 的关键描述），
因为字幕/时间轴对齐依赖它的输出边界（含标点、压平换行、去引号）。
"""
from __future__ import annotations

import re


def split_text(text: str, mode: str = "sentence") -> list[str]:
    """按句末标点或段落切分文案（逐字对齐参考工程 _split_text）。

    sentence: 去除直/弯双引号、压平换行，按 。！？ 切分并保留标点。
    paragraph: 按空行（段落）切分。
    """
    text = text.strip()
    # 去除直双引号与弯双引号
    text = re.sub(r'["\u201c\u201d]', "", text)

    if mode == "paragraph":
        raw = re.split(r"\n\s*\n+", text)
        return [s.strip() for s in raw if s.strip()]

    text = re.sub(r"\n", "", text)  # 压平换行
    parts = re.split(r"(?<=[。！？])", text)
    segments = [s.strip() for s in parts if s.strip()]
    return segments if segments else [text]
