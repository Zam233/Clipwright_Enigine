"""配音编排（从 engine 的 `VoiceService.dub_script` 迁出，Batch 3 / B3-T4）。

**迁出的是编排，不是协议**：
- 切句（`services.voice.split_text`）与逐段合成后的时间轴拼接、确定性 seed、
  句级/词级时间戳归一 —— 这一整套**属于功能层**，现在归本文件；
- 单段合成（provider 解析、上传器注入、音频落盘、ffprobe 实测时长）仍是 engine 的
  `VoiceService.synthesize()` —— PRD §15.1.2 明确 engine 保留「统一协议调度」。

**为什么 seed 用 sha256 而不是 hash()**：Python 内置 `hash()` 对 str 带进程级随机盐
（PYTHONHASHSEED），同一文案跨进程会得到不同 seed，合成结果不可复现。engine 那边
踩过一次（`voice.py:912-915` 的注释），这里保持同一算法，避免插件与内置行为漂移。
"""
from __future__ import annotations

import hashlib
from typing import Any

from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.skill.base import BaseSkill


class DubScriptOrchestrator(BaseSkill):
    """`dub_script` 技能：把文案切成片段并逐段合成，返回带时间轴的 segments。"""

    name = "dub_script"
    description = "文案切分配音（插件实现）：按句/段切分，逐段合成并拼接句级时间轴"
    required_tools: list[str] = []

    def is_available(self) -> bool:
        """只要有任意 TTS provider 能解析出来就算可用。

        ⚠️ 必须是**同步**方法：`BaseSkill.is_available` 是同步契约，
        `SkillRegistry._get_skill_info()` 直接把返回值塞进 `SkillInfo.available: bool`
        ——写成 `async def` 会把 coroutine 对象塞进 pydantic 字段，启动时直接
        `ValidationError`（本轮踩过）。

        能力缺失是一等状态（不变量 #19）：这里返回 False 时调用方会看到
        「没有可用的配音能力」，而不是一个跑到一半才炸的异常。
        """
        try:
            from clipwright.providers.tts import tts_provider_names

            return bool(tts_provider_names())
        except Exception:  # noqa: BLE001 — 解析失败按不可用处理
            return False

    async def execute(
        self,
        *,
        voice_id: str,
        text: str,
        split_mode: str = "sentence",
        provider: str = "",
        target_model: str = "",
        instructions: str = "",
        **params: Any,
    ) -> SkillExecResult:
        from clipwright.services.voice import get_voice_service, split_text

        if not text.strip():
            return self._fail("文本为空，无可配音内容", voice_id)

        segments_text = split_text(text, split_mode)
        if not segments_text or all(not s.strip() for s in segments_text):
            return self._fail("文本为空，无可配音内容", voice_id)

        svc = get_voice_service()
        results: list[dict[str, Any]] = []
        cursor = 0.0
        total_duration = 0.0

        for idx, seg in enumerate(segments_text):
            seed = int(
                hashlib.sha256(f"{provider}:{voice_id}:{seg}".encode("utf-8")).hexdigest()[:8],
                16,
            )
            try:
                sr = await svc.synthesize(
                    voice_id=voice_id,
                    text=seg,
                    provider=provider,
                    target_model=target_model,
                    instructions=instructions,
                    **{**params, "seed": seed},
                )
                if not sr.success:
                    results.append({"index": idx, "text": seg, "error": sr.error})
                    continue
                d = dict(sr.data)
                d["index"] = idx
                d["seed"] = seed
                seg_dur = float(d.get("duration_sec", 0) or 0)
                d["start_sec"] = round(cursor, 3)
                d["end_sec"] = round(cursor + seg_dur, 3)
                words = d.get("word_timestamps", [])
                if words:
                    d["words"] = [
                        {"word": w.get("word", ""), "t": round(cursor + float(w.get("time", 0)) / 1000.0, 3)}
                        for w in words
                    ]
                    d["char_timings"] = [w["t"] for w in d["words"]]
                else:
                    chars = [c for c in seg if not c.isspace()]
                    if chars and seg_dur > 0:
                        d["char_timings"] = [
                            round(cursor + seg_dur * (i + 0.5) / len(chars), 3)
                            for i in range(len(chars))
                        ]
                cursor += seg_dur
                total_duration += seg_dur
                results.append(d)
            except Exception as e:  # noqa: BLE001 — 单段失败不拖垮整篇
                results.append({"index": idx, "text": seg, "error": str(e)[:200]})

        all_failed = bool(results) and all("error" in r for r in results)
        first_err = next((r["error"] for r in results if "error" in r), None)
        if all_failed:
            return SkillExecResult(
                status=SkillStatus.ERROR,
                skill_name=self.name,
                error=str(first_err or "语音合成全部失败"),
            )
        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "segments": results,
                "total": len(results),
                "total_duration_sec": round(total_duration, 2),
                "provider": provider,
            },
        )

    def _fail(self, message: str, voice_id: str) -> SkillExecResult:
        return SkillExecResult(
            status=SkillStatus.ERROR,
            skill_name=self.name,
            error=f"{message}（voice_id={voice_id or '未指定'}）",
        )
