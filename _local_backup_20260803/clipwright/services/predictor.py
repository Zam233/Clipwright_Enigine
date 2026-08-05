"""智能预判服务 — 自动分析文稿/素材，推荐操作。"""

from __future__ import annotations

import re
from typing import Any

from clipwright.config import logger
from clipwright.services.llm import LLMService


class ScriptAnalyzer:
    """文稿分析器 — 自动估算时长、检测语言类型、推荐 Persona 和类型插件。"""

    @staticmethod
    async def analyze(script_text: str) -> dict[str, Any]:
        """分析文稿并返回预判结果。"""
        chars = len(script_text)
        sentences = len(re.split(r'[。！？.!?\n]', script_text))
        paragraphs = len([p for p in script_text.split('\n') if p.strip()])

        # 估算时长：中文约 4 字/秒
        estimated_duration = max(30, chars / 4)
        # 限制最长 1800s
        estimated_duration = min(estimated_duration, 1800)

        # 检测是否有英文/中英文混合
        has_english = bool(re.search(r'[a-zA-Z]{3,}', script_text))

        # 用 LLM 推荐 Persona 和类型插件
        recommendations = await ScriptAnalyzer._llm_recommend(script_text, chars)

        return {
            "char_count": chars,
            "sentence_count": sentences,
            "paragraph_count": paragraphs,
            "estimated_duration_sec": round(estimated_duration, 1),
            "has_english": has_english,
            "video_mode": "voiceover" if len(re.split(r'[。！？]', script_text)) > 5 else "visual",
            **recommendations,
        }

    @staticmethod
    async def _llm_recommend(script: str, chars: int) -> dict:
        """LLM 推荐 Persona 和类型插件。"""
        preview = script[:500]
        try:
            llm = LLMService()
            resp = await llm.ask(
                f"分析以下文稿，推荐最适合的视频类型配置。\n\n"
                f"文稿预览: {preview}\n总字数: {chars}\n\n"
                f"可用 Persona: zam_knowledge_critical(知识区批判型), zam_digital_cool(数码评测炫酷型)\n"
                f"可用类型插件: knowledge_longform(知识区长片), digital_review(数码评测), "
                f"kichiku_fastcut(鬼畜快剪), vlog_daily(Vlog日常)\n\n"
                f"返回 JSON 格式:\n"
                f"{{\n"
                f'  "recommended_persona": "persona_id",\n'
                f'  "recommended_plugin": "plugin_id",\n'
                f'  "reason": "推荐理由",\n'
                f'  "estimated_mood": "positive/negative/neutral/epic",\n'
                f'  "key_topics": ["主题1", "主题2"]\n'
                f"}}"
            )
            if resp.success and resp.content:
                import json
                content = resp.content.strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    content = "\n".join(lines[1:-1])
                return json.loads(content)
        except Exception as e:
            logger.debug("LLM 推荐失败: %s", e)

        return {
            "recommended_persona": "zam_knowledge_critical",
            "recommended_plugin": "knowledge_longform",
            "reason": "自动选择（LLM 不可用）",
            "estimated_mood": "neutral",
            "key_topics": [],
        }


class MaterialAnalyzer:
    """素材分析器 — 上传时自动检测并推荐使用方式。"""

    @staticmethod
    async def analyze(file_path: str, file_size: int) -> dict[str, Any]:
        """分析素材并推荐使用方式。"""
        from clipwright.services.material_preprocessor import MaterialPreprocessor
        meta = await MaterialPreprocessor._analyze_content(file_path)
        duration = meta.get("duration", 0)
        width = meta.get("width", 0)
        height = meta.get("height", 0)

        # 判断类型
        is_landscape = width > height if width and height else True
        has_audio = any(s.get("type") == "audio" for s in meta.get("streams", []))
        has_video = any(s.get("type") == "video" for s in meta.get("streams", []))

        recommendations = []
        if duration > 300:
            recommendations.append("长素材 → 建议用 scene_detect 自动切分场景")
        if not has_video and has_audio:
            recommendations.append("纯音频 → 可作为配音或 BGM")
        if is_landscape and width >= 1920:
            recommendations.append("高清横屏素材 → 适合主视频轨")
        if not is_landscape and width:
            recommendations.append("竖屏素材 → 适合 PiP 画中画或竖屏项目")
        if not recommendations:
            recommendations.append("通用素材 → 可用于 B-roll")

        return {
            "duration_sec": round(duration, 1),
            "resolution": f"{width}x{height}" if width and height else "未知",
            "is_landscape": is_landscape,
            "has_video": has_video,
            "has_audio": has_audio,
            "recommendations": recommendations,
            "streams": meta.get("streams", []),
        }
