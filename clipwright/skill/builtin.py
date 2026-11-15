"""内置技能（Built-in Skills）——已全部迁出 engine。

2026-11-14 skill 插件化（PRD docs/skill-pluginization-prd-2026-11-14.md）：
engine **只负责提供接入能力**（`BaseSkill` 契约、`SkillRegistry`、工具面接线、
插件贡献契约），真正的业务技能全部通过插件接入。

- `analyze_video_structure` / `generate_captions` / `analyze_audio_rhythm`
  → `plugins/skill_pack`（内置插件）
- `dub_script` → `plugins/tts_feature`（2026-11-03 已迁出）

本函数保留为**兼容入口**（历史调用点仍会 import）：现在只记录一条 debug 日志，
不再向 `SkillRegistry` 注册任何业务技能。没有装对应插件时，技能面为空是
**一等状态**，不是需要内置兜底的缺陷。
"""

from __future__ import annotations


def register_builtin_skills() -> None:
    """空实现：engine 不再内置业务 Skill（技能由插件贡献）。"""
    from clipwright.config import logger

    logger.debug(
        "register_builtin_skills(): engine 不内置业务技能；"
        "技能由插件提供（如 plugins/skill_pack）",
    )
