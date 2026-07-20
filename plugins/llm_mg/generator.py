"""MG 动画 LLM 生成器 — LLM 生成 → 验证 → 修复 → 降级。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from clipwright.services.llm import LLMService
from clipwright.config import logger

from .validator import validate_mg_json, repair_mg_json
from .fallback import FallbackEngine
from .storage import MGStorage


class MGGenerator:
    """LLM 驱动的 MG 动画生成器。"""

    def __init__(self) -> None:
        self._llm = LLMService()
        self._storage = MGStorage()
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        config_path = Path(__file__).resolve().parent / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    async def generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成 MG 动画 JSON。

        Returns:
            {success, html, mg_def, method, fallback_template, generation_id}
        """
        persona_style = persona_style or {}
        scene_context = scene_context or {}

        gen_config = self._config.get("generation", {})
        max_retries = gen_config.get("max_retries", 2)

        # ── ① LLM 生成 ──
        mg_def = None
        for attempt in range(max_retries + 1):
            try:
                mg_def = await self._call_llm(description, text_content, persona_style, scene_context)
                if mg_def:
                    break
            except Exception as e:
                logger.warning("MGGenerator LLM attempt %d failed: %s", attempt + 1, e)

        # ── ② 验证 + 修复 ──
        if mg_def:
            ok, errors = validate_mg_json(mg_def)
            if not ok:
                logger.warning("MGGenerator validation errors: %s", errors)
                mg_def, fixes = repair_mg_json(mg_def)
                logger.info("MGGenerator repair fixes: %s", fixes)

            ok2, errors2 = validate_mg_json(mg_def)
            if ok2:
                return self._build_success(mg_def, "llm")

        # ── ③ 降级到已有模板 ──
        return await self._fallback_generate(description, text_content, persona_style)

    async def _call_llm(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
    ) -> dict[str, Any] | None:
        """调用 LLM 生成 MG JSON。"""
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")

        user_parts = [f"## 动画需求\n{description}"]
        if text_content:
            user_parts.append(f"## 文字内容\n{text_content}")
        if persona_style:
            style_desc = persona_style.get("style_description", "")
            primary = persona_style.get("primary_color", "")
            if style_desc or primary:
                user_parts.append(f"## 风格要求\n{style_desc}\n主色: {primary}")
        if scene_context:
            title = scene_context.get("title", "")
            keywords = scene_context.get("keywords", [])
            if title or keywords:
                user_parts.append(f"## 场景上下文\n标题: {title}\n关键词: {keywords}")

        user_prompt = "\n\n".join(user_parts)

        llm_config = self._config.get("llm", {})
        try:
            response = await self._llm.generate(
                messages=[
                    {"role": "system", "content": system_template},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=llm_config.get("temperature", 0.3),
                timeout=llm_config.get("timeout", 60),
            )
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_llm_response(content)
        except Exception as e:
            logger.warning("MGGenerator LLM call failed: %s", e)
            return None

    def _parse_llm_response(self, content: str) -> dict[str, Any] | None:
        """从 LLM 响应中提取 JSON。"""
        if not content:
            return None

        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("MGGenerator: could not parse JSON from LLM response: %.200s", content)
        return None

    async def _fallback_generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
    ) -> dict[str, Any]:
        """降级生成 — 匹配已有模板。"""
        templates = self._storage.get_templates()
        best = FallbackEngine.find_best_template(description, templates)

        if best:
            # 从存储加载完整模板（而非仅元信息）
            full_template = self._storage.load_template(best.get("animation_id", ""))
            if full_template:
                template, params = FallbackEngine.fill_template_params(full_template, text_content, persona_style)
                return self._build_success(template, "fallback", fallback_template=best.get("animation_id"))

        logger.warning("MGGenerator: no fallback template available")
        return {
            "success": False,
            "html": "",
            "mg_def": {},
            "method": "fallback",
            "fallback_template": None,
            "generation_id": "",
        }

    def _build_success(
        self, mg_def: dict, method: str, fallback_template: str | None = None,
    ) -> dict[str, Any]:
        """构建成功响应 + 渲染 HTML。"""
        result = self._storage.save_generation(mg_def)
        generation_id = result["generation_id"]

        from clipwright.animation.mg_renderer import MGRenderer
        try:
            html = MGRenderer.render(mg_def)
        except Exception as e:
            logger.warning("MGGenerator: MGRenderer.render() failed: %s", e)
            html = ""

        return {
            "success": bool(html),
            "html": html,
            "mg_def": mg_def,
            "method": method,
            "fallback_template": fallback_template,
            "generation_id": generation_id,
        }
