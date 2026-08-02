"""MG 动画 LLM 生成器 — LLM 生成 → 验证 → 修复 → 降级。

内置模块，不依赖插件加载器。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from clipwright.services.llm import LLMService
from clipwright.config import logger

from clipwright.animation.mg.validator import validate_mg_json, repair_mg_json
from clipwright.animation.mg.fallback import FallbackEngine


class MGGenerator:
    """LLM 驱动的 MG 动画生成器。"""

    def __init__(self) -> None:
        self._llm = LLMService()
        self._config = self._load_config()

    @staticmethod
    def _load_config() -> dict[str, Any]:
        config_path = Path(__file__).resolve().parent / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _get_templates(self) -> list[dict]:
        """获取模板列表（从内置 templates 目录）。"""
        from clipwright.animation.mg import list_templates as list_mg_templates
        return list_mg_templates()

    @staticmethod
    def _trace_event(
        pipeline_id: str | None, event_type: str, summary: str, detail: Any = None,
    ) -> None:
        """记录一条 MG 追踪事件（仅观察用途，不影响生成流程）。

        pipeline_id 不可用（None/空）时静默跳过；异常被吞掉避免干扰生成。
        事件形状: agent="mg", event_type, summary, detail。
        """
        if not pipeline_id:
            return
        try:
            from clipwright.services.trace import add_event
            add_event(pipeline_id, "mg", event_type, summary, detail)
        except Exception:
            pass

    async def generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
        category_context: dict[str, Any] | None = None,
        pipeline_id: str | None = None,
    ) -> dict[str, Any]:
        """生成 MG 动画 JSON。

        Args:
            description: 自然语言动画需求描述
            text_content: 动画中的文本内容（| 分隔多段）
            persona_style: Persona 视觉风格参数（StyleInterpreter 输出）
            scene_context: 当前场景上下文 {title, keywords}
            category_context: 视频类型（category）特征数据
                {plugin_id, display_name, description, shot_params, pacing,
                 mg_style_guidance, translated}

        Returns:
            {success, html, mg_def, method, fallback_template, generation_id}
        """
        persona_style = persona_style or {}
        scene_context = scene_context or {}
        category_context = category_context or {}

        gen_config = self._config.get("generation", {})
        max_retries = gen_config.get("max_retries", 2)

        mg_def = None
        llm_fail_reasons: list[str] = []
        # 正常生成：至多 1 次盲重试（盲重试很贵，2-4 分钟/次）。
        # 解析/校验失败改为走下面的「带错误回传的修复重试」，比盲重试高效得多。
        for attempt in range(min(max_retries, 1) + 1):
            try:
                mg_def = await self._call_llm(
                    description, text_content, persona_style,
                    scene_context, category_context,
                    pipeline_id=pipeline_id,
                )
                if mg_def:
                    break
            except Exception as e:
                reason = str(e) or repr(e)
                llm_fail_reasons.append(reason)
                logger.warning("MGGenerator LLM attempt %d failed: %s", attempt + 1, e)
                self._trace_event(
                    pipeline_id, "llm_attempt_fail",
                    f"LLM 生成第 {attempt + 1} 次失败",
                    {"attempt": attempt + 1, "error": reason[:200]},
                )

        if mg_def:
            ok, errors = validate_mg_json(mg_def)
            if not ok:
                logger.warning("MGGenerator validation errors: %s", errors)
                self._trace_event(
                    pipeline_id, "validation_error",
                    "MG JSON 校验失败",
                    {"errors": errors[:20]},
                )
                mg_def, fixes = repair_mg_json(mg_def)
                logger.info("MGGenerator repair fixes: %s", fixes)
                self._trace_event(
                    pipeline_id, "repair",
                    "MG JSON 自动修复",
                    {"fixes": fixes},
                )

            ok2, errors2 = validate_mg_json(mg_def)
            if not ok2:
                # 带错误回传的一次修复重试（仅一次，避免无限重试）
                repaired = await self._call_llm_repair(
                    mg_def, errors2, description, text_content,
                    persona_style, scene_context, category_context,
                    pipeline_id=pipeline_id,
                )
                if repaired:
                    logger.info("MGGenerator: 修复重试成功（带错误回传）")
                    return self._build_success(repaired, "llm_repair", pipeline_id=pipeline_id)
                logger.warning("MGGenerator: 修复重试仍失败，进入降级")
                self._trace_event(
                    pipeline_id, "llm_repair_fail",
                    "修复重试仍失败，进入降级",
                    {"errors": errors2[:20]},
                )
            else:
                return self._build_success(mg_def, "llm", pipeline_id=pipeline_id)

        if not mg_def:
            # LLM 多次尝试均失败（返回 None 或抛异常），记录失败原因供降级决策追溯
            self._trace_event(
                pipeline_id, "llm_fail",
                "LLM 生成失败（多次尝试均未返回有效 JSON）",
                {"reasons": llm_fail_reasons[-5:]},
            )
        return await self._fallback_generate(
            description, text_content, persona_style, pipeline_id=pipeline_id,
        )

    async def _call_llm_repair(
        self,
        broken_def: dict[str, Any],
        errors: list[str],
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        pipeline_id: str | None = None,
    ) -> dict[str, Any] | None:
        """带错误回传的一次性修复调用：把 schema 校验错误告诉 LLM 让其修正 JSON。"""
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")
        context_section = self._build_context_section(persona_style, category_context)
        system_prompt = system_template
        if context_section:
            system_prompt += "\n\n" + context_section
        system_prompt += (
            "\n\n上次输出的动画 JSON 未通过 schema 校验。请只输出**修正后的完整 JSON**，"
            "确保它通过全部校验规则，不要输出任何解释或额外文本。"
        )

        broken_str = json.dumps(broken_def, ensure_ascii=False)
        user_parts = [f"## 动画需求\n{description}"]
        if text_content:
            user_parts.append(f"## 文字内容\n{text_content}")
        user_parts.append("## 校验错误\n" + "\n".join(f"- {e}" for e in errors))
        user_parts.append(f"## 上次输出（不合法）\n{broken_str[:4000]}")
        user_prompt = "\n\n".join(user_parts)

        llm_config = self._config.get("llm", {})
        try:
            response = await self._llm.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=llm_config.get("temperature", 0.2),
                timeout=llm_config.get("timeout", 120),
            )
            content = response.content if hasattr(response, "content") else str(response)
            repaired = self._parse_llm_response(content)
            if repaired:
                ok, _ = validate_mg_json(repaired)
                if ok:
                    return repaired
        except Exception as e:
            logger.warning("MGGenerator repair call failed: %s", e)
            self._trace_event(
                pipeline_id, "llm_repair_call_fail",
                "修复重试 LLM 调用失败",
                {"error": str(e)[:200]},
            )
        return None

    async def _call_llm(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        pipeline_id: str | None = None,
    ) -> dict[str, Any] | None:
        """调用 LLM 生成 MG JSON。

        系统提示词 = 基础规范模板（config.yaml）+ 动态上下文段落
        （Persona 视觉风格数据 + 视频类型特征数据）。
        动画风格不写死，由 LLM 依据传入数据自行决定。
        """
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")

        context_section = self._build_context_section(persona_style, category_context)
        system_prompt = system_template
        if context_section:
            system_prompt += "\n\n" + context_section

        user_parts = [f"## 动画需求\n{description}"]
        if text_content:
            user_parts.append(f"## 文字内容\n{text_content}")
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=llm_config.get("temperature", 0.4),
                timeout=llm_config.get("timeout", 120),
            )
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_llm_response(content)
        except Exception as e:
            logger.warning("MGGenerator LLM call failed: %s", e)
            self._trace_event(
                pipeline_id, "llm_call_fail",
                "LLM 调用失败",
                {"error": str(e)[:200]},
            )
            return None

    @staticmethod
    def _build_context_section(persona_style: dict, category_context: dict) -> str:
        """构建动态上下文段落（Persona 视觉风格 + 视频类型特征）。

        将 Persona 视觉风格与视频类型（category）的结构化数据转为文本，
        注入生成 prompt，由 LLM 自行决定动画设计。
        """
        parts: list[str] = []

        # ── Persona 视觉风格 ──
        fields = [
            ("primary_color", "主色"), ("secondary_color", "辅色"),
            ("accent_color", "强调色"), ("text_color", "文字色"),
            ("font_size", "正文字号"), ("title_font_size", "标题字号"),
            ("font", "字体"), ("palette", "配色描述"),
            ("style_description", "风格描述"), ("style_preset", "风格预设"),
        ]
        collected = [
            f"- {label}: {persona_style[k]}"
            for k, label in fields if persona_style.get(k)
        ]
        if collected:
            parts.append(
                "## 当前创作者（Persona）视觉风格\n"
                "动画的配色、字体、整体气质必须与此保持一致：\n" + "\n".join(collected)
            )

        # ── 视频类型（category）特征 ──
        cat_parts: list[str] = []
        name = category_context.get("display_name") or category_context.get("plugin_id", "")
        if name:
            cat_parts.append(f"- 视频类型: {name}")
        desc = category_context.get("description")
        if desc:
            cat_parts.append(f"- 类型特征: {desc}")
        shot = category_context.get("shot_params")
        if isinstance(shot, dict) and shot:
            shot_str = "、".join(f"{k}={v}" for k, v in shot.items() if v)
            if shot_str:
                cat_parts.append(f"- 镜头特征: {shot_str}")
        pacing = category_context.get("pacing")
        if isinstance(pacing, dict) and pacing:
            pacing_str = "、".join(f"{k}={v}" for k, v in pacing.items() if v)
            if pacing_str:
                cat_parts.append(f"- 节奏特征: {pacing_str}")
        guidance = category_context.get("mg_style_guidance")
        if guidance:
            cat_parts.append(f"- 动画风格指引: {guidance}")
        brief_style = category_context.get("brief_animation_style")
        if isinstance(brief_style, dict) and brief_style:
            style_items = []
            for k, label in (("style", "风格"), ("tone", "色调"), ("fonts", "字体"), ("icons", "图标")):
                v = brief_style.get(k)
                if v:
                    if isinstance(v, dict):
                        v = "、".join(f"{fk}:{fv}" for fk, fv in v.items() if fv)
                    style_items.append(f"{label}: {v}")
            if style_items:
                cat_parts.append(
                    "- 简报动画风格（用户确认，优先遵循）: " + "；".join(style_items)
                )
        ratio = category_context.get("brief_asset_ratio")
        if isinstance(ratio, dict) and (ratio.get("footage") or ratio.get("mg")):
            cat_parts.append(f"- 简报素材/动画占比: 实拍 {ratio.get('footage', '')} · MG {ratio.get('mg', '')}")
        if cat_parts:
            parts.append(
                "## 当前视频类型（category）特征\n"
                "动画设计与该视频类型的剪辑节奏、气质相协调：\n" + "\n".join(cat_parts)
            )

        return "\n\n".join(parts)

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
        pipeline_id: str | None = None,
    ) -> dict[str, Any]:
        """降级生成 — 匹配已有模板。"""
        templates = self._get_templates()
        best = FallbackEngine.find_best_template(description, templates)

        if best:
            from pathlib import Path as _Path
            import json as _json
            tid = best.get("animation_id", "")
            templates_dir = _Path(__file__).resolve().parent / "templates"
            template_path = templates_dir / f"{tid}.json"
            if template_path.exists():
                try:
                    full_template = _json.loads(template_path.read_text(encoding="utf-8"))
                    template, params = FallbackEngine.fill_template_params(full_template, text_content, persona_style)
                    self._trace_event(
                        pipeline_id, "fallback_template",
                        f"降级命中模板 {tid}",
                        {"fallback_template": tid},
                    )
                    return self._build_success(
                        template, "fallback", fallback_template=tid, pipeline_id=pipeline_id,
                    )
                except Exception:
                    pass

        logger.warning(
            "MGGenerator: no fallback template available — LLM 与模板均失败，降级为 drawtext 文字显示"
        )
        self._trace_event(
            pipeline_id, "fallback_fail",
            "无可用降级模板，最终降级为 drawtext 文字显示",
            {"method": "drawtext", "fallback_template": None},
        )
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
        pipeline_id: str | None = None,
    ) -> dict[str, Any]:
        """构建成功响应 + 渲染 HTML。"""
        import uuid
        from datetime import datetime

        generation_id = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        from clipwright.animation.mg_renderer import MGRenderer
        try:
            html = MGRenderer.render(mg_def)
        except Exception as e:
            logger.warning("MGGenerator: MGRenderer.render() failed: %s", e)
            html = ""

        # 记录最终生成方式（llm / llm_repair / fallback），供 trace/SSE 展示与质检追溯
        self._trace_event(
            pipeline_id, "method",
            f"MG 生成完成 method={method}",
            {
                "method": method,
                "fallback_template": fallback_template,
                "generation_id": generation_id,
                "success": bool(html),
            },
        )

        return {
            "success": bool(html),
            "html": html,
            "mg_def": mg_def,
            "method": method,
            "fallback_template": fallback_template,
            "generation_id": generation_id,
        }
