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


# 残留占位符检测：{word} 形式的字面量在最终 HTML 中出现即视为未填充
_RESIDUAL_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


# LLM MG 生成/修复提示词共用：输出硬性约束（降低 fallback 率，run6 有 6 次 fallback）
_STRICT_JSON_OUTPUT = (
    "\n\n## 输出硬性要求\n"
    "- 只输出一个合法的 JSON 对象，不要包含任何解释性文字、前言或后语。\n"
    "- 不要使用 markdown 代码块围栏（``` 或 ```json），直接输出原始 JSON。\n"
    "- 输出中除 JSON 外不要出现任何其他字符。\n"
)

# 背景约束：MG 动画叠加在实拍素材上，默认禁止不透明全幅背景层。
# 仅当创作者在 vision_prompt 中明确要求背景时才允许 bg 元素。
_NO_BACKGROUND_CONSTRAINT = (
    "禁止背景层：除非 vision_prompt 明确要求背景，动画不得包含 bg 元素或任何"
    "不透明全幅背景（background 必须为 transparent）；动画叠加在实拍素材上。"
)


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

    async def generate(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
        category_context: dict[str, Any] | None = None,
        vision_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
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
            vision_prompt: Persona 视觉需求 Prompt（非空时注入 LLM 上下文，
                空串不产生额外段落）
            width: 拟定分辨率宽度（时间线尺寸；None 时回退 mg_def/1920）
            height: 拟定分辨率高度（时间线尺寸；None 时回退 mg_def/1080）
            fps: 实际时间线帧率

        Returns:
            {success, html, mg_def, method, fallback_template, generation_id}
        """
        persona_style = persona_style or {}
        scene_context = scene_context or {}
        category_context = category_context or {}

        gen_config = self._config.get("generation", {})
        max_retries = gen_config.get("max_retries", 2)

        mg_def = None
        # 正常生成：至多 1 次盲重试（盲重试很贵，2-4 分钟/次）。
        # 解析/校验失败改为走下面的「带错误回传的修复重试」，比盲重试高效得多。
        for attempt in range(min(max_retries, 1) + 1):
            try:
                mg_def = await self._call_llm(
                    description, text_content, persona_style,
                    scene_context, category_context, vision_prompt,
                )
                if mg_def:
                    break
            except Exception as e:
                logger.warning("MGGenerator LLM attempt %d failed: %s", attempt + 1, e)

        if mg_def:
            ok, errors = validate_mg_json(mg_def)
            if not ok:
                logger.warning("MGGenerator validation errors: %s", errors)
                mg_def, fixes = repair_mg_json(mg_def)
                logger.info("MGGenerator repair fixes: %s", fixes)
                ok, errors = validate_mg_json(mg_def)

            if ok:
                # 校验通过 → LLM 自批判质量闭环：低分且可修复 → 一次修复重试
                result = await self._finalize_with_critique(
                    mg_def, "llm", description, text_content,
                    persona_style, scene_context, category_context, vision_prompt,
                    width=width, height=height, fps=fps,
                )
                if result:
                    return result
                logger.warning("MGGenerator: 批判修复失败，进入降级")
                return await self._fallback_generate(
                    description, text_content, persona_style,
                    width=width, height=height, fps=fps,
                    vision_prompt=vision_prompt,
                )

            # 带错误回传的一次修复重试（仅一次，避免无限重试）
            repaired = await self._call_llm_repair(
                mg_def, errors, description, text_content,
                persona_style, scene_context, category_context, vision_prompt,
            )
            if repaired:
                logger.info("MGGenerator: 修复重试成功（带错误回传）")
                result = await self._finalize_with_critique(
                    repaired, "llm_repair", description, text_content,
                    persona_style, scene_context, category_context, vision_prompt,
                    width=width, height=height, fps=fps,
                )
                if result:
                    return result
                logger.warning("MGGenerator: 修复重试结果批判不通过，进入降级")
                return await self._fallback_generate(
                    description, text_content, persona_style,
                    width=width, height=height, fps=fps,
                    vision_prompt=vision_prompt,
                )
            logger.warning("MGGenerator: 修复重试仍失败，进入降级")

        return await self._fallback_generate(
            description, text_content, persona_style,
            width=width, height=height, fps=fps,
            vision_prompt=vision_prompt,
        )

    @staticmethod
    def _ensure_no_background(mg_def: dict[str, Any], vision_prompt: str = "") -> dict[str, Any]:
        """背景守卫：vision_prompt 为空时，强制 bg 元素背景透明。

        MG 动画叠加在实拍素材上，默认不允许不透明全幅背景；
        仅当创作者在 vision_prompt 中明确要求背景时保留 bg 元素原样。
        """
        if not isinstance(mg_def, dict):
            return mg_def
        if vision_prompt and str(vision_prompt).strip():
            return mg_def
        elements = mg_def.get("elements")
        if isinstance(elements, list):
            for elem in elements:
                if isinstance(elem, dict) and elem.get("type") == "bg":
                    if elem.get("background") not in (None, "transparent"):
                        elem["background"] = "transparent"
        return mg_def

    async def _call_llm_repair(
        self,
        broken_def: dict[str, Any],
        errors: list[str],
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        vision_prompt: str = "",
    ) -> dict[str, Any] | None:
        """带错误回传的一次性修复调用：把 schema 校验错误告诉 LLM 让其修正 JSON。"""
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")
        context_section = self._build_context_section(persona_style, category_context, vision_prompt)
        system_prompt = system_template
        if context_section:
            system_prompt += "\n\n" + context_section
        system_prompt += (
            "\n\n上次输出的动画 JSON 未通过 schema 校验。请只输出**修正后的完整 JSON**，"
            "确保它通过全部校验规则，不要输出任何解释或额外文本。"
        )
        system_prompt += _STRICT_JSON_OUTPUT

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
        return None

    async def _finalize_with_critique(
        self,
        mg_def: dict[str, Any],
        method: str,
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        vision_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
    ) -> dict[str, Any] | None:
        """校验通过后的质量出口：LLM 自批判 + 必要时一次修复重试。

        返回成功响应；批判修复失败时返回 None（调用方进入降级）。
        批判 LLM 调用失败/禁用时静默跳过批判，直接接受当前输出（不降级）。
        """
        critique = await self._critique_quality(
            mg_def, description, persona_style, category_context, vision_prompt,
        )
        params = self._build_llm_params(mg_def, text_content, persona_style)
        if critique is None:
            # LLM 评估失败（或 critique 禁用）→ 静默跳过，不降级
            return await self._build_success(mg_def, method, params=params,
                                       width=width, height=height, fps=fps,
                                       description=description,
                                       text_content=text_content,
                                       persona_style=persona_style,
                                       vision_prompt=vision_prompt)

        score = critique.get("score", 100)
        issues = critique.get("issues", [])
        suggestions = critique.get("suggestions", [])
        min_score = self._config.get("critique", {}).get("min_score", 60)

        if score >= min_score:
            logger.debug("MGGenerator: 批判通过 score=%d", score)
            return await self._build_success(mg_def, method, params=params,
                                       width=width, height=height, fps=fps,
                                       description=description,
                                       text_content=text_content,
                                       persona_style=persona_style,
                                       vision_prompt=vision_prompt)

        if not self._issues_fixable(issues, suggestions):
            logger.warning("MGGenerator: 批判低分(score=%d)但问题不可修复，接受原输出", score)
            return await self._build_success(mg_def, method, params=params,
                                       width=width, height=height, fps=fps,
                                       description=description,
                                       text_content=text_content,
                                       persona_style=persona_style,
                                       vision_prompt=vision_prompt)

        # 低分且可修复 → 带批判反馈的一次修复重试（仅一次）
        repaired = await self._call_llm_critique_repair(
            mg_def, critique, description, text_content,
            persona_style, scene_context, category_context, vision_prompt,
        )
        if repaired:
            logger.info("MGGenerator: 批判修复成功 score=%d", score)
            return await self._build_success(
                repaired, "critique_repair",
                params=self._build_llm_params(repaired, text_content, persona_style),
                width=width, height=height, fps=fps,
                description=description, text_content=text_content,
                persona_style=persona_style,
                vision_prompt=vision_prompt,
            )

        logger.warning("MGGenerator: 批判修复失败(score=%d)，进入降级", score)
        return None

    async def _critique_quality(
        self,
        mg_def: dict[str, Any],
        description: str,
        persona_style: dict,
        category_context: dict,
        vision_prompt: str = "",
    ) -> dict[str, Any] | None:
        """LLM 自批判质量评估。

        让 LLM 按 config.yaml 中的设计原则与硬性约束对已生成的 mg_def 打分，
        输出 {score 0-100, issues[], suggestions[]}。评估失败（LLM 异常/解析
        失败/score 越界）返回 None，调用方应静默跳过（不降级）。

        Returns:
            {"score": int, "issues": list[str], "suggestions": list[str]} 或 None
        """
        critique_config = self._config.get("critique", {})
        if not critique_config.get("enabled", True):
            return None

        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "")
        context_section = self._build_context_section(persona_style, category_context, vision_prompt)

        system_prompt = (
            "你是一位严苛的 Motion Graphics 动画质量评审专家。"
            "下面给出 MG 动画 JSON 及其设计原则与硬性约束，请据此评审动画质量。\n\n"
        )
        if system_template:
            system_prompt += system_template
        system_prompt += (
            "\n\n## 评审输出\n"
            "只输出一个 JSON，不要包含解释性文字：\n"
            '{"score": 0到100的整数, "issues": ["具体问题1", ...], '
            '"suggestions": ["可操作建议1", ...]}\n'
            "- score < 60 表示未达标（元素过少/缺少缓动/违反硬性约束/动画平淡单调等）。\n"
            "- issues 指出违反的原则与约束，具体到元素或关键帧。\n"
            "- suggestions 给出一到两条可执行的改进方向，供修复重试使用。\n"
        )
        if context_section:
            system_prompt += "\n\n" + context_section

        user_parts = [f"## 动画需求\n{description}"]
        user_parts.append(
            "## 待评审动画 JSON\n" + json.dumps(mg_def, ensure_ascii=False)[:4000]
        )
        user_prompt = "\n\n".join(user_parts)

        llm_config = self._config.get("llm", {})
        try:
            response = await self._llm.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=critique_config.get("temperature", 0.1),
                timeout=llm_config.get("timeout", 120),
            )
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_critique(content)
        except Exception as e:
            logger.warning("MGGenerator critique call failed: %s", e)
            return None

    async def _call_llm_critique_repair(
        self,
        mg_def: dict[str, Any],
        critique: dict[str, Any],
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        vision_prompt: str = "",
    ) -> dict[str, Any] | None:
        """带批判反馈的一次性修复调用：把质量评审意见告诉 LLM 让其重做 JSON。"""
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")
        context_section = self._build_context_section(persona_style, category_context, vision_prompt)
        system_prompt = system_template
        if context_section:
            system_prompt += "\n\n" + context_section
        system_prompt += (
            "\n\n上次输出的动画 JSON 经质量评审未达标（score 低于阈值）。"
            "请根据评审意见重新设计，只输出**改进后的完整 JSON**，"
            "确保它通过全部校验规则并修复所有质量问题，不要输出任何解释或额外文本。"
        )
        system_prompt += _STRICT_JSON_OUTPUT

        broken_str = json.dumps(mg_def, ensure_ascii=False)
        issues = "\n".join(f"- {i}" for i in critique.get("issues", []))
        suggestions = "\n".join(f"- {s}" for s in critique.get("suggestions", []))
        user_parts = [f"## 动画需求\n{description}"]
        if text_content:
            user_parts.append(f"## 文字内容\n{text_content}")
        user_parts.append(f"## 质量评审\n评分: {critique.get('score')}")
        if issues:
            user_parts.append("问题:\n" + issues)
        if suggestions:
            user_parts.append("改进建议:\n" + suggestions)
        user_parts.append(f"## 上次输出（未达标）\n{broken_str[:4000]}")
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
            logger.warning("MGGenerator critique repair call failed: %s", e)
        return None

    def _parse_critique(self, content: str) -> dict[str, Any] | None:
        """从 LLM 批判响应中提取并规范化 {score, issues, suggestions}。"""
        parsed = self._parse_llm_response(content)
        if not isinstance(parsed, dict):
            return None

        raw_score = parsed.get("score")
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            logger.warning("MGGenerator: critique 评分无法解析: %r", raw_score)
            return None
        if not (0 <= score <= 100):
            logger.warning("MGGenerator: critique 评分越界: %s", score)
            return None

        def _to_str_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(v) for v in value if str(v).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            return []

        return {
            "score": score,
            "issues": _to_str_list(parsed.get("issues")),
            "suggestions": _to_str_list(parsed.get("suggestions")),
        }

    @staticmethod
    def _issues_fixable(issues: list[str], suggestions: list[str]) -> bool:
        """判断批判问题是否可修复：有可操作建议，或问题非根本性不可修复。"""
        if suggestions:
            return True
        if not issues:
            return False
        unfixable_markers = ("无法修复", "不可能", "需求不足", "信息不足", "无法实现", "无解")
        return not any(
            marker in issue for issue in issues for marker in unfixable_markers
        )

    async def _call_llm(
        self,
        description: str,
        text_content: str,
        persona_style: dict,
        scene_context: dict,
        category_context: dict,
        vision_prompt: str = "",
    ) -> dict[str, Any] | None:
        """调用 LLM 生成 MG JSON。

        系统提示词 = 基础规范模板（config.yaml）+ 动态上下文段落
        （Persona 视觉风格数据 + 视频类型特征数据 + 视觉需求 Prompt）。
        动画风格不写死，由 LLM 依据传入数据自行决定。
        """
        prompt_config = self._config.get("prompt", {})
        system_template = prompt_config.get("system_template", "Generate MG animation JSON.")

        context_section = self._build_context_section(persona_style, category_context, vision_prompt)
        system_prompt = system_template
        if context_section:
            system_prompt += "\n\n" + context_section
        system_prompt += _STRICT_JSON_OUTPUT

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
            return None

    @staticmethod
    def _build_context_section(
        persona_style: dict, category_context: dict, vision_prompt: str = "",
    ) -> str:
        """构建动态上下文段落（Persona 视觉风格 + 视频类型特征 + 视觉需求）。

        将 Persona 视觉风格与视频类型（category）的结构化数据转为文本，
        注入生成 prompt，由 LLM 自行决定动画设计。vision_prompt 非空时
        追加「视觉需求」段落，空串不产生额外输出（保持向后兼容）。
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

        # ── 视觉需求（vision_prompt，创作者定义，优先级最高）──
        if vision_prompt:
            parts.append(
                "## 视觉需求（vision_prompt）\n"
                "动画必须满足以下视觉需求，优先级高于默认设计：\n" + str(vision_prompt)
            )

        # ── 最终约束（Q2：色板注入值是最终准绳，置于所有内容段落之后）──
        # 放在最末覆盖上方所有色调建议（含「简报动画风格」「动画风格指引」），
        # 明确禁止默认蓝紫科技渐变/发光粒子/彩虹渐变——否则 LLM 易按兜底蓝紫生成。
        _palette_colors = {
            "主色": persona_style.get("primary_color"),
            "辅色": persona_style.get("secondary_color"),
            "强调色": persona_style.get("accent_color"),
        }
        _color_lines = [
            f"- {k}: {v}" for k, v in _palette_colors.items() if v
        ]
        if _color_lines:
            parts.append(
                "## 最终约束\n"
                "色板以 Persona 注入值为最终准绳。无论上方任何段落（含「简报动画风格」"
                "「动画风格指引」）如何建议色调，以本段色板为准。\n"
                + "\n".join(_color_lines) +
                "\n" + _NO_BACKGROUND_CONSTRAINT +
                "\n禁止默认蓝紫科技渐变、发光粒子、彩虹渐变。"
            )
        else:
            # 无注入色板时仍需输出背景约束（默认禁止不透明背景层）
            parts.append("## 最终约束\n" + _NO_BACKGROUND_CONSTRAINT)

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
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
        vision_prompt: str = "",
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
                    return await self._build_success(template, "fallback", fallback_template=tid, params=params,
                                               width=width, height=height, fps=fps,
                                               vision_prompt=vision_prompt)
                except Exception:
                    pass

        logger.warning("MGGenerator: no fallback template available")
        return {
            "success": False,
            "html": "",
            "mg_def": {},
            "method": "fallback",
            "fallback_template": None,
            "generation_id": "",
        }

    @staticmethod
    def _build_llm_params(
        mg_def: dict[str, Any],
        text_content: str,
        persona_style: dict | None = None,
    ) -> dict[str, str]:
        """为 LLM 生成的 mg_def 构建占位符参数。

        LLM 输出可能在元素内容中引用 {text} {left} {right} {accent} 等占位符。
        union 收集键：先取模板 params 定义键，再扫描内容中出现的 {key} 占位符并集，
        按位置用 | 分隔的文本段填充，并应用 Persona 主色覆盖 accent，
        避免占位符原样渲染进最终 HTML。
        """
        style = persona_style or {}
        parts = FallbackEngine.extract_keywords(text_content)
        param_defs = mg_def.get("params", {})

        # 双保护(a): union 扫描 — 无论是否声明 params，都扫描 mg_def 内容里的
        # {placeholder} 并与其 params 键取并集。避免「有 params 键即跳过内容扫描」
        # 导致 {left}/{right}/{accent} 等未声明键残留字面量。顺序稳定：params 键优先，
        # 再追加内容扫描出的新键。
        param_keys: list[str] = []
        seen: set[str] = set()
        for key in param_defs:
            if key not in seen:
                seen.add(key)
                param_keys.append(key)
        for m in re.findall(
            r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", json.dumps(mg_def, ensure_ascii=False)
        ):
            if m not in seen:
                seen.add(m)
                param_keys.append(m)

        params: dict[str, str] = {}
        for i, key in enumerate(param_keys):
            if i < len(parts):
                params[key] = parts[i]
            else:
                default = param_defs.get(key)
                params[key] = default.get("default", "") if isinstance(default, dict) else ""

        if parts:
            params["text"] = parts[0]

        if "primary_color" in style and "accent" in params:
            # Persona 主色覆盖默认 accent
            params["accent"] = style["primary_color"]

        return params

    async def _render_html_no_residuals(
        self,
        mg_def: dict,
        params: dict[str, Any] | None,
        description: str | None = None,
        text_content: str | None = None,
        persona_style: dict | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
        allow_fallback: bool = True,
    ) -> str:
        """渲染并确保输出不含字面占位符（{key}）。

        双保护(c): 渲染后扫描残留占位符，若有则用 mg_def 全部占位符键补齐 params
        再渲一次（二次填充）；仍残留且允许降级时改用 fallback 模板渲染（fallback
        路径本身已把模板参数填满，输出无占位符）。作为守卫只记录日志并修复，不循环。
        """
        from clipwright.animation.mg_renderer import MGRenderer
        params = params or {}
        try:
            html = MGRenderer.render(mg_def, params, width=width, height=height, fps=fps)
        except Exception as e:
            logger.warning("MGGenerator: MGRenderer.render() failed: %s", e)
            html = ""

        if html and not _RESIDUAL_PLACEHOLDER_RE.search(html):
            return html

        # 残留 → 二次填充：把 mg_def 中出现的所有占位符键补齐（默认值或空串）再渲染
        param_defs = mg_def.get("params", {})
        union_keys: list[str] = []
        seen: set[str] = set()
        for key in param_defs:
            if key not in seen:
                seen.add(key)
                union_keys.append(key)
        for m in _RESIDUAL_PLACEHOLDER_RE.findall(json.dumps(mg_def, ensure_ascii=False)):
            if m not in seen:
                seen.add(m)
                union_keys.append(m)

        merged = dict(params)
        for key in union_keys:
            if key not in merged:
                default = param_defs.get(key)
                merged[key] = default.get("default", "") if isinstance(default, dict) else ""
        try:
            html2 = MGRenderer.render(mg_def, merged, width=width, height=height, fps=fps)
        except Exception as e:
            logger.warning("MGGenerator: 残留占位符二次渲染失败: %s", e)
            html2 = ""
        if html2 and not _RESIDUAL_PLACEHOLDER_RE.search(html2):
            logger.warning("MGGenerator: 渲染残留占位符经二次填充后清除")
            return html2

        if allow_fallback and description and text_content:
            try:
                fallback = await self._fallback_generate(
                    description, text_content, persona_style or {},
                    width=width, height=height, fps=fps,
                )
                fb_html = fallback.get("html") or ""
                if fb_html and not _RESIDUAL_PLACEHOLDER_RE.search(fb_html):
                    logger.warning("MGGenerator: 渲染残留占位符 → 降级模板渲染")
                    return fb_html
            except Exception as e:
                logger.warning("MGGenerator: 残留占位符降级渲染失败: %s", e)

        logger.warning("MGGenerator: 渲染后仍含残留占位符，返回原始 HTML")
        return html or html2

    @staticmethod
    def _ensure_label_separation(mg_def: dict[str, Any]) -> dict[str, Any]:
        """标签分离守卫：LLM 生成的对比模板可能把 left/right 标签放在同一坐标。

        质检发现 mg_generated_cost_asymmetry 的 {left_label}/{right_label} 都在
        x=center,y=center,y_offset=240 → 渲染后两个标签完全重叠（不可读）。
        此处对坐标完全相同的 text 元素，按内容占位符语义（left/right）强制分列左右。
        """
        if not isinstance(mg_def, dict):
            return mg_def
        elements = mg_def.get("elements")
        if not isinstance(elements, list):
            return mg_def

        from collections import defaultdict
        groups: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
        for i, e in enumerate(elements):
            if isinstance(e, dict) and e.get("type") == "text":
                key = (e.get("x"), e.get("y"), e.get("y_offset"), e.get("x_offset"))
                groups[key].append((i, e))

        for _key, items in groups.items():
            if len(items) < 2:
                continue
            for idx, e in items:
                content = str(e.get("content") or "")
                low = content.lower()
                if "left" in low:
                    e["x"] = "left"
                elif "right" in low:
                    e["x"] = "right"
        return mg_def

    async def _build_success(
        self, mg_def: dict, method: str, fallback_template: str | None = None,
        params: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float = 30.0,
        description: str | None = None,
        text_content: str | None = None,
        persona_style: dict | None = None,
        vision_prompt: str = "",
    ) -> dict[str, Any]:
        """构建成功响应 + 渲染 HTML（含残留占位符兜底）。"""
        import uuid
        from datetime import datetime

        # 背景守卫：vision_prompt 未明确要求背景时剥离不透明 bg 背景层
        mg_def = self._ensure_no_background(mg_def, vision_prompt)
        # 标签分离守卫：left/right 标签同坐标 → 按语义分列左右（质检发现的重叠 bug）
        mg_def = self._ensure_label_separation(mg_def)

        generation_id = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        html = await self._render_html_no_residuals(
            mg_def, params,
            description=description, text_content=text_content, persona_style=persona_style,
            width=width, height=height, fps=fps,
            # fallback 路径自身已填满模板参数，禁止再次降级以免递归
            allow_fallback=(method != "fallback"),
        )

        return {
            "success": bool(html),
            "html": html,
            "mg_def": mg_def,
            "method": method,
            "fallback_template": fallback_template,
            "generation_id": generation_id,
        }
