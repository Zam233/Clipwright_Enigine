"""StyleInterpreter — 将 Persona 视觉参数转为图解样式。

核心流程：
1. 查找是否有 StyleInterpreterPlugin 插件已注册（多个时按 priority 取活跃项）
2. 如果有 → 委派给插件
3. 如果没有 → 使用内置 LLM 驱动解释器
4. 结构化字段（如 primary_color）优先级最高，覆盖 LLM 结果
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger
from clipwright.plugins.base import StyleInterpreterPlugin


class StyleInterpreter:
    """风格解释器调度器（多注册 + 优先级调度 + 按 owner 回收）。

    D3 修复：此前是「单槽 + 第二个 owner 直接 ValueError」——两台机器/两个插件
    各自提供风格解释器时，后安装的那个**装不上**（贡献被拒，用户以为装了却无效）。
    现在改为多注册表：

    - 每个 owner 一个槽位，互不覆盖（`register_plugin` 不再抛错）；
    - 运行时按 `priority` 选活跃项（越大越优先；同优先级先注册者胜，结果确定）；
    - `unregister_plugin(owner)` 只回收自己的槽位，活跃位自动改选；
    - `_plugin`/`_owner` 保留为「当前活跃项」的只读兼容视图（既有读取方无须改）。
    """

    #: owner → (priority, plugin)；owner="__builtin__"/"" 表示内置/无归属注册
    _plugins: dict[str, tuple[int, StyleInterpreterPlugin]] = {}
    #: 注册顺序（同优先级时先注册者胜，保证调度确定）
    _order: list[str] = []
    #: 当前活跃项（兼容视图，随后按优先级同步）
    _plugin: Optional[StyleInterpreterPlugin] = None
    _owner: str = ""
    #: 无归属注册使用的槽位名（与内置贡献镜像的 owner 对齐）
    _DEFAULT_SLOT = "__default__"

    @classmethod
    def register_plugin(cls, plugin: StyleInterpreterPlugin, owner: str = "",
                        priority: int = 100) -> None:
        """注册（或替换本 owner 的）风格解释器插件。**不再拒绝其它 owner**。"""
        slot = str(owner or "") or cls._DEFAULT_SLOT
        if slot not in cls._order:
            cls._order.append(slot)
        cls._plugins[slot] = (int(priority), plugin)
        cls._refresh_active()
        logger.info("StyleInterpreter: 已注册插件 %s (owner=%s, priority=%s, 共 %d 个)",
                    plugin.manifest.id, slot, priority, len(cls._plugins))

    @classmethod
    def unregister_plugin(cls, owner: str = "") -> bool:
        """按 owner 回收**该 owner 自己的**槽位（插件禁用/卸载）。返回是否回收。"""
        slot = str(owner or "") or cls._DEFAULT_SLOT
        if slot not in cls._plugins:
            return False  # 不是它的槽位，拒绝误回收
        cls._plugins.pop(slot, None)
        cls._order = [o for o in cls._order if o != slot]
        cls._refresh_active()
        logger.info("StyleInterpreter: 已回收插件（owner=%s，剩 %d 个）", slot, len(cls._plugins))
        return True

    @classmethod
    def _refresh_active(cls) -> None:
        """按 (priority 降序, 注册顺序升序) 选出活跃项，并同步兼容视图。"""
        if not cls._plugins:
            cls._plugin = None
            cls._owner = ""
            return
        ranked = sorted(
            cls._order,
            key=lambda o: (-cls._plugins[o][0], cls._order.index(o)),
        )
        active = next((o for o in ranked if o in cls._plugins), "")
        cls._owner = active
        cls._plugin = cls._plugins[active][1] if active else None

    @classmethod
    def list_plugins(cls) -> list[tuple[str, int, StyleInterpreterPlugin]]:
        """已注册的 (owner, priority, plugin)，按活跃度排序（管理面/镜像用）。"""
        cls._refresh_active()
        return [
            (o, cls._plugins[o][0], cls._plugins[o][1])
            for o in sorted(cls._order, key=lambda x: (-cls._plugins[x][0], cls._order.index(x)))
            if o in cls._plugins
        ]

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._plugins.clear()
        cls._order.clear()
        cls._refresh_active()

    @classmethod
    async def interpret(
        cls,
        visual_config: dict[str, Any],
        persona_context: dict[str, Any],
    ) -> dict[str, Any]:
        """解释 Persona 视觉参数，返回 DiagramStyle 兼容的字典。

        Args:
            visual_config: Persona visual 层（palette/font/style_description 等）
            persona_context: 完整 Persona 上下文（identity/language/rhythm 等）

        Returns:
            DiagramStyle 兼容参数字典
        """
        # 1. 优先用插件解释器（D3：多注册场景下用当前活跃项）
        cls._refresh_active()
        if cls._plugin is not None:
            try:
                result = await cls._plugin.interpret(visual_config, persona_context)
                if result:
                    # 结构化字段覆盖插件结果
                    result = cls._apply_structured_overrides(result, visual_config)
                    logger.debug("StyleInterpreter: 插件返回 %s", result)
                    return result
            except Exception as e:
                logger.warning("StyleInterpreter: 插件异常 %s，回退到默认", e)

        # 2. 无插件 → LLM 驱动解释
        result = await cls._llm_interpret(visual_config, persona_context)

        # 3. 结构化字段覆盖
        result = cls._apply_structured_overrides(result, visual_config)
        return result

    @classmethod
    async def _llm_interpret(
        cls,
        visual_config: dict[str, Any],
        persona_context: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM 解析自然语言风格描述。"""
        # 收集可用的自然语言字段
        palette = visual_config.get("palette", "")
        font_desc = visual_config.get("font", "")
        style_desc = visual_config.get("style_description", "")
        tone = persona_context.get("identity", {}).get("tone", "")

        # 如果有精确的结构化字段，直接返回（无需 LLM）
        has_exact = any(
            visual_config.get(k) for k in (
                "primary_color", "secondary_color", "font_size",
            )
        )
        if has_exact and not style_desc and not palette:
            return {
                "primary_color": visual_config.get("primary_color", "#4f8cff"),
                "secondary_color": visual_config.get("secondary_color", "#ff6b6b"),
                "accent_color": visual_config.get("accent_color", "#fbbf24"),
                "text_color": visual_config.get("font_color", "#ffffff"),
                "font_size": visual_config.get("font_size", 28),
                "title_font_size": visual_config.get("title_font_size", 36),
                "stagger_delay": visual_config.get("stagger_delay", 0.25),
            }

        # 没有精确值 + 无自然语言描述 → 按 tone 回退
        if not style_desc and not palette and not font_desc:
            return cls._tone_fallback(tone)

        # 有自然语言描述 → 调 LLM 解析
        vision_prompt = persona_context.get("vision_prompt", "") or ""
        prompt = (
            f"你是一个视频图解风格设计师。请根据以下描述返回 JSON。\n\n"
            f"内容基调: {tone or '通用'}\n"
            f"配色描述: {palette or '未指定'}\n"
            f"字体描述: {font_desc or '未指定'}\n"
            f"风格描述: {style_desc or '未指定'}\n\n"
        )
        # 问题3补充：vision_prompt.md 注入风格解析——视觉需求（纯黑纯白底/红色强调等）
        # 影响文字动画/字幕等 persona_style 消费点（此前仅 LLM 动态 MG 用 vision_prompt）。
        if vision_prompt:
            prompt += (
                "## 创作者视觉需求（vision_prompt，最高优先级）\n"
                f"{vision_prompt[:1500]}\n\n"
            )
        prompt += (
            f"返回以下 JSON（只返回 JSON，不要其他文字）：\n"
            f"{{\n"
            f'  "primary_color": "主色 #RRGGBB",\n'
            f'  "secondary_color": "辅色 #RRGGBB",\n'
            f'  "accent_color": "强调色 #RRGGBB",\n'
            f'  "text_color": "文字色 #RRGGBB",\n'
            f'  "font_size": 28,\n'
            f'  "title_font_size": 36,\n'
            f'  "stagger_delay": 0.25,\n'
            f'  "font": "字体描述",\n'
            f'  "reason": "简短的设计理由"\n'
            f"}}"
        )

        try:
            plugin_prompts = persona_context.get("_plugin_prompts", [])
            if plugin_prompts:
                prompt += "\n\n## 插件能力扩展\n" + "\n\n".join(plugin_prompts)

            from clipwright.services.llm import LLMService
            llm = LLMService()
            resp = await llm.ask(prompt, temperature=0.3)
            if resp.success and resp.content:
                import json, re
                content = resp.content.strip()
                # 提取 JSON
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                    logger.info("StyleInterpreter LLM: %s",
                                parsed.get("reason", parsed.get("primary_color", ""))[:80])
                    return {
                        "primary_color": parsed.get("primary_color", "#4f8cff"),
                        "secondary_color": parsed.get("secondary_color", "#ff6b6b"),
                        "accent_color": parsed.get("accent_color", "#fbbf24"),
                        "text_color": parsed.get("text_color", "#ffffff"),
                        "font_size": parsed.get("font_size", 28),
                        "title_font_size": parsed.get("title_font_size", 36),
                        "stagger_delay": parsed.get("stagger_delay", 0.25),
                        "font": parsed.get("font", ""),
                    }
        except Exception as e:
            logger.warning("StyleInterpreter LLM 失败: %s", e)

        return cls._tone_fallback(tone)

    @classmethod
    def _apply_structured_overrides(
        cls, base: dict[str, Any], visual_config: dict[str, Any],
    ) -> dict[str, Any]:
        """结构化字段覆盖 LLM/插件结果。"""
        overrides = {
            "primary_color": visual_config.get("primary_color"),
            "secondary_color": visual_config.get("secondary_color"),
            "accent_color": visual_config.get("accent_color"),
            "text_color": visual_config.get("font_color"),
            "font_size": visual_config.get("font_size"),
            "title_font_size": visual_config.get("title_font_size"),
            "stagger_delay": visual_config.get("stagger_delay"),
        }
        for k, v in overrides.items():
            if v is not None:
                base[k] = v
        return base

    @classmethod
    def _tone_fallback(cls, tone: str) -> dict[str, Any]:
        """按 tone 回退到一套合理的默认值。"""
        tone_lower = tone.lower() if tone else ""

        if any(w in tone_lower for w in ["批判", "冷峻", "理性", "严肃", "科技"]):
            return {
                "primary_color": "#3b82f6", "secondary_color": "#64748b",
                "accent_color": "#f59e0b", "text_color": "#f1f5f9",
                "font_size": 28, "title_font_size": 36, "stagger_delay": 0.25,
                "font": "sans-serif",
            }
        if any(w in tone_lower for w in ["热情", "温暖", "幽默", "吐槽", "轻松"]):
            return {
                "primary_color": "#ea580c", "secondary_color": "#f97316",
                "accent_color": "#fbbf24", "text_color": "#fff7ed",
                "font_size": 30, "title_font_size": 38, "stagger_delay": 0.2,
                "font": "sans-serif",
            }
        if any(w in tone_lower for w in ["学术", "深度", "严肃"]):
            return {
                "primary_color": "#1e3a5f", "secondary_color": "#4a5568",
                "accent_color": "#c5a55a", "text_color": "#e2e8f0",
                "font_size": 26, "title_font_size": 34, "stagger_delay": 0.3,
                "font": "serif",
            }
        # 默认
        return {
            "primary_color": "#4f8cff", "secondary_color": "#ff6b6b",
            "accent_color": "#fbbf24", "text_color": "#ffffff",
            "font_size": 28, "title_font_size": 36, "stagger_delay": 0.25,
            "font": "sans-serif",
        }
