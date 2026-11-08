"""歌词数据模型（PRD C5 / R-11）。

只表达"哪一行、哪几个词、哪几个音节、哪个是关键词"，**不存歌词原文**
（原文由 ``text(i)`` 取，避免版权与一致性问题，见 PRD §II.5）。

层级：``LyricsDoc → LyricLine → LyricWord``；``syllables`` 用于"逐块印上"（结巴画法）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class LyricWord(BaseModel):
    """行内的词（可标记为关键词＝该乐句的视觉高点）。"""

    w: int = 0
    t0: float = 0.0
    t1: float = 0.0
    key: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)


class LyricLine(BaseModel):
    """一行歌词。``text_ref`` 是 ``text(i)`` 的索引，不是歌词原文。"""

    i: int = 0
    t0: float = 0.0
    t1: float = 0.0
    text_ref: str = "text(0)"
    words: list[LyricWord] = Field(default_factory=list)
    syllables: list[float] = Field(default_factory=list)
    band: bool = True  # 是否走字幕带（关键词镜头不重复走带）

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    def keywords(self) -> list[LyricWord]:
        return [word for word in self.words if word.key]

    def readable_for(self, t: float) -> bool:
        return self.t0 <= t < self.t1


class LyricsDoc(BaseModel):
    """一份歌词时间表。"""

    language: str = ""
    lines: list[LyricLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_order(self) -> "LyricsDoc":
        previous_end = -1.0
        for line in self.lines:
            if line.t1 < line.t0:
                raise ValueError(f"第 {line.i} 行 t1 < t0")
            if line.t0 < previous_end - 1e-6:
                raise ValueError(f"第 {line.i} 行与前一行重叠")
            previous_end = max(previous_end, line.t1)
        return self

    def line_at(self, t: float) -> Optional[LyricLine]:
        for line in self.lines:
            if line.readable_for(t):
                return line
        return None

    def band_text_ref(self, t: float) -> Optional[str]:
        """当前字幕带应显示的 ``text_ref``（不返回歌词原文）。"""
        line = self.line_at(t)
        if line is None or not line.band:
            return None
        return line.text_ref


def parse_lyrics(payload: dict[str, Any]) -> LyricsDoc:
    """从 JSON 结构构造（供 cues/场景脚本/测试共用）。"""
    return LyricsDoc(**payload)


__all__ = ["LyricLine", "LyricWord", "LyricsDoc", "parse_lyrics"]
