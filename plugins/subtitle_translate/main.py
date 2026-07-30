"""字幕翻译插件 — 翻译字幕文本，生成双语字幕轨道。

支持翻译引擎：
  - "llm": 使用内置 LLMService（默认）
  - "deepl": DeepL API（需 DEEPL_API_KEY）

注册 Tool（subtitle_translate）和 Skill（bilingual_subtitle）。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.skill.base import BaseSkill
from clipwright.skill.registry import SkillRegistry
from clipwright.schema.skill import SkillExecResult
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class SubtitleTranslateTool(BaseTool):
    name = "subtitle_translate"
    description = "翻译字幕文本到目标语言"
    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要翻译的文本"},
            "target_lang": {"type": "string", "description": "目标语言代码（en/zh/ja/ko 等）"},
            "source_lang": {"type": "string", "description": "源语言代码（留空自动检测）"},
        },
        "required": ["text", "target_lang"],
    }

    def __init__(self, engine: str = "llm") -> None:
        self._engine = engine

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        text = kwargs.get("text", "")
        target = kwargs.get("target_lang", "en")
        source = kwargs.get("source_lang", "")
        if not text:
            return {"success": False, "error": "缺少 text"}
        try:
            if self._engine == "deepl":
                return await self._translate_deepl(text, target, source)
            return await self._translate_llm(text, target, source)
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _translate_llm(self, text: str, target: str, source: str) -> dict:
        from clipwright.services.llm import LLMService
        llm = LLMService()
        prompt = f"Translate the following text to {target}. Only output the translation, nothing else.\n\n{text}"
        result = await llm.ask(prompt)
        return {"success": True, "translated": result.strip(), "engine": "llm"}

    async def _translate_deepl(self, text: str, target: str, source: str) -> dict:
        key = os.environ.get("DEEPL_API_KEY", "")
        if not key:
            return {"success": False, "error": "DEEPL_API_KEY 未配置"}
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post("https://api-free.deepl.com/v2/translate",
                headers={"Authorization": f"DeepL-Auth-Key {key}"},
                data={"text": text, "target_lang": target.upper(), **({"source_lang": source.upper()} if source else {})})
            resp.raise_for_status()
            translated = resp.json()["translations"][0]["text"]
            return {"success": True, "translated": translated, "engine": "deepl"}


class BilingualSubtitleSkill(BaseSkill):
    name = "bilingual_subtitle"
    description = "将字幕 clip 列表翻译为双语字幕（原文 + 译文）"
    required_tools: list[str] = []

    async def execute(self, **kwargs) -> SkillExecResult:
        clips = kwargs.get("clips", [])
        target = kwargs.get("target_lang", "en")
        tool = SubtitleTranslateTool()
        results = []
        for clip in clips:
            text = clip.get("text", "")
            if not text:
                results.append(clip)
                continue
            tr = await tool.execute(text=text, target_lang=target)
            translated = tr.get("translated", "") if tr.get("success") else ""
            results.append({**clip, "text": f"{text}\n{translated}" if translated else text})
        return SkillExecResult(status="success", skill_name=self.name, output={"clips": results})


class SubtitleTranslatePlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="subtitle_translate", name="Subtitle Translation", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Translate subtitles via LLM or DeepL API",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        engine = (self.config or {}).get("engine", "llm")
        ToolRegistry.register(SubtitleTranslateTool(engine=engine), plugin_id=self.manifest.id)
        SkillRegistry.register(BilingualSubtitleSkill(), plugin_id=self.manifest.id)
        logger.info("[SubtitleTranslate] Tool + Skill 已注册 (engine=%s)", engine)

    def shutdown(self) -> None:
        pass


__all__ = ["SubtitleTranslatePlugin"]
