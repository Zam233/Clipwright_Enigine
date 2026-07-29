"""智能预判服务 — LLM 分析文稿/素材并推荐 Persona、类型、时长等。

优先使用 LLM 进行语义级判断；LLM 不可用时回退到关键词启发式。
"""

from __future__ import annotations

import json
from typing import Any

from clipwright.config import logger
from clipwright.services.llm import LLMService

_llm = LLMService()

# ---------------------------------------------------------------------------
# 文稿分析
# ---------------------------------------------------------------------------

_SCRIPT_SYSTEM = """\
你是一个视频内容策划专家。用户会给你一段文稿，你需要分析并返回 JSON：
{
  "video_type": "knowledge_longform | kichiku_fastcut | digital_review | vlog_daily",
  "estimated_duration_sec": <int, 预估口播时长(秒)>,
  "recommended_persona_tone": "<推荐语气风格，如 warm_storyteller / energetic / analytical / humorous>",
  "summary": "<一句话概括文稿主题>",
  "key_topics": ["<关键话题1>", "<关键话题2>"]
}
只返回 JSON，不要其他内容。"""

_VIDEO_TYPES = ("knowledge_longform", "kichiku_fastcut", "digital_review", "vlog_daily")


class ScriptAnalyzer:
    """分析文稿，推荐视频类型、Persona 风格和预估时长。"""

    @staticmethod
    async def analyze(script_text: str) -> dict:
        text_len = len(script_text)

        # 尝试 LLM 分析
        try:
            resp = await _llm.generate(
                messages=[
                    {"role": "system", "content": _SCRIPT_SYSTEM},
                    {"role": "user", "content": script_text[:4000]},  # 截断避免超长
                ],
                timeout=30,
            )
            if resp.success and resp.content:
                data = _parse_json(resp.content)
                if data and data.get("video_type") in _VIDEO_TYPES:
                    data["char_count"] = text_len
                    data["source"] = "llm"
                    logger.info("ScriptAnalyzer(LLM): type=%s, ~%ss",
                                data["video_type"], data.get("estimated_duration_sec"))
                    return data
        except Exception as e:
            logger.warning("ScriptAnalyzer LLM 失败，回退启发式: %s", e)

        # 启发式兜底
        result = _heuristic_script(script_text)
        result["source"] = "heuristic"
        return result


# ---------------------------------------------------------------------------
# 素材分析
# ---------------------------------------------------------------------------

_MATERIAL_SYSTEM = """\
你是一个视频素材管理专家。用户会给你一个素材文件的信息（路径、大小），你需要分析并返回 JSON：
{
  "suggested_usage": "video_track | audio_track | overlay_or_background | b_roll | title_card",
  "quality_hint": "<根据文件大小推测质量: high / medium / low>",
  "editing_tips": "<一句话剪辑建议>"
}
只返回 JSON，不要其他内容。"""


class MaterialAnalyzer:
    """分析素材文件，推荐使用方式。"""

    @staticmethod
    async def analyze(file_path: str, file_size: int = 0) -> dict:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        is_video = ext in ("mp4", "mov", "avi", "mkv", "webm", "flv")
        is_audio = ext in ("mp3", "wav", "aac", "flac", "ogg")
        is_image = ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp")

        base_result: dict[str, Any] = {
            "file_path": file_path,
            "file_size": file_size,
            "extension": ext,
            "is_video": is_video,
            "is_audio": is_audio,
            "is_image": is_image,
        }

        # 尝试 LLM 分析（仅对视频/图片素材有额外价值）
        if is_video or is_image:
            try:
                prompt = f"文件: {file_path}\n大小: {file_size} bytes ({file_size / 1024 / 1024:.1f} MB)\n类型: {'视频' if is_video else '图片'}"
                resp = await _llm.generate(
                    messages=[
                        {"role": "system", "content": _MATERIAL_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=20,
                )
                if resp.success and resp.content:
                    data = _parse_json(resp.content)
                    if data:
                        base_result.update(data)
                        base_result["source"] = "llm"
                        logger.info("MaterialAnalyzer(LLM): %s -> %s", file_path, data.get("suggested_usage"))
                        return base_result
            except Exception as e:
                logger.warning("MaterialAnalyzer LLM 失败，回退启发式: %s", e)

        # 启发式兜底
        if is_video:
            usage = "video_track"
        elif is_audio:
            usage = "audio_track"
        elif is_image:
            usage = "overlay_or_background"
        else:
            usage = "unknown"

        base_result["suggested_usage"] = usage
        base_result["source"] = "heuristic"
        logger.info("MaterialAnalyzer(heuristic): %s -> %s", file_path, usage)
        return base_result


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON（容忍 markdown 代码块包裹）。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _heuristic_script(text: str) -> dict:
    """关键词启发式分析（LLM 不可用时的兜底）。"""
    text_len = len(text)
    estimated_duration_sec = max(30, text_len // 4)
    lower = text.lower()

    if any(kw in lower for kw in ("鬼畜", "快剪", "踩点", "remix")):
        video_type = "kichiku_fastcut"
    elif any(kw in lower for kw in ("评测", "开箱", "体验", "对比", "参数")):
        video_type = "digital_review"
    elif any(kw in lower for kw in ("vlog", "日常", "记录", "一天")):
        video_type = "vlog_daily"
    else:
        video_type = "knowledge_longform"

    if any(kw in text for kw in ("！", "哈哈", "绝了", "离谱")):
        tone = "energetic"
    elif any(kw in text for kw in ("首先", "其次", "综上", "因此")):
        tone = "analytical"
    else:
        tone = "warm_storyteller"

    logger.info("ScriptAnalyzer(heuristic): %d chars -> type=%s, ~%ds", text_len, video_type, estimated_duration_sec)
    return {
        "video_type": video_type,
        "estimated_duration_sec": estimated_duration_sec,
        "recommended_persona_tone": tone,
        "char_count": text_len,
        "summary": text[:80],
        "key_topics": [],
    }
