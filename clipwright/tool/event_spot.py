"""领域事件定位工具（R3）——把"找 XX 那一段"变成可执行的时间戳。

两个能力：
- `mode=text`：对一段文本/转写找事件（词表命中 + 证据句 + 置信度）；
- `mode=audio`：对素材做音频事件检测（静音/大响段/起始沿），并对齐到镜头。

为什么做成 Tool：结构/剪辑/质检三个 Agent 都要用同一把尺子——"用户说的那些事在哪几秒"
是它们共同的输入；工具化之后也便于评测（同一批输入给不同实现打分）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class EventSpotTool(BaseTool):
    """领域事件定位（文本词表 / 音频能量）。"""

    name = "event_spot"
    description = (
        "定位素材里的领域事件：mode=text 用领域词表在文本/转写里找事件（进球/开箱/价格/结论…），"
        "mode=audio 检测音频事件（静音/大响段/起始沿）并按镜头对齐。"
        "返回事件的时间范围与证据，供剪辑直接落位"
    )
    dependencies = []

    async def execute(
        self,
        mode: str = "text",
        *,
        text: str = "",
        segments: Optional[list[dict[str, Any]]] = None,
        media_path: str = "",
        shot_ranges: Optional[list[list[float]]] = None,
        domain: str = "generic",
        extra_lexicon: str = "",
        language: str = "zh",
        max_events: int = 40,
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services.event_lexicon import (
            EVENT_LABELS,
            VALID_DOMAINS,
            resolve_lexicon,
            spot_events_in_text,
        )

        act = (mode or "text").strip().lower()
        if domain and domain not in VALID_DOMAINS:
            logger.info("event_spot: 未知领域 %r，回退 generic", domain)
        lexicon = resolve_lexicon(domain, extra_lexicon)

        try:
            if act == "text":
                events: list[dict[str, Any]] = []
                if segments:
                    from clipwright.services.content_audio import segments_to_phrases

                    for ph in segments_to_phrases(segments):
                        events.extend(spot_events_in_text(
                            ph["text"], lexicon=lexicon, t0=ph["t0"], t1=ph["t1"],
                            source="segment"))
                if text:
                    events.extend(spot_events_in_text(text, lexicon=lexicon, source="text"))
                if not events:
                    return ToolExecResult(
                        status=ToolStatus.SUCCESS, tool_name=self.name,
                        output={"mode": "text", "domain": domain, "events": [], "count": 0,
                                "note": "未命中任何领域事件词"})
                events.sort(key=lambda ev: (ev["t0"], -ev["confidence"]))
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"mode": "text", "domain": domain,
                                              "events": events[:max(1, int(max_events))],
                                              "count": len(events),
                                              "labels": EVENT_LABELS})

            if act == "audio":
                from clipwright.services.content_audio import (
                    detect_audio_events, events_for_shot, extract_audio_wav,
                    merge_events, read_wav_mono, summarize_audio_events,
                )

                if not media_path or not Path(media_path).exists():
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error=f"media_path 不存在: {media_path!r}")
                work = Path(media_path)
                wav = await extract_audio_wav(work, work.parent / f"{work.stem}.audio.wav")
                if wav is None:
                    return ToolExecResult(
                        status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name,
                        error="素材没有音轨或音频抽取失败（ffmpeg 缺失/无音轨）")
                signal, sr = read_wav_mono(wav)
                if signal is None or sr <= 0:
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error="音频解码失败")
                events = merge_events(detect_audio_events(signal, sr))
                per_shot = []
                for rng in shot_ranges or []:
                    if not isinstance(rng, (list, tuple)) or len(rng) < 2:
                        continue
                    per_shot.append({
                        "t0": float(rng[0]), "t1": float(rng[1]),
                        "events": events_for_shot(events, float(rng[0]), float(rng[1])),
                    })
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
                    "mode": "audio",
                    "events": events[:max(1, int(max_events))],
                    "count": len(events),
                    "summary": summarize_audio_events(events),
                    "per_shot": per_shot,
                    "duration_sec": round(len(signal) / sr, 3),
                })

            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"未知 mode: {mode!r}（可用：text / audio）")
        except Exception as exc:
            logger.warning("event_spot 工具失败: %s", exc)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))
