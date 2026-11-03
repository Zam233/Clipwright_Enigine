"""TTS 功能层插件（Batch 3 / B3-T4）。

**它接管什么**：`dub_script` 技能——也就是 `agent.call_skill("dub")` 真正落地的地方
（能力别名 `dub → dub_script`，见 `tool/capabilities.py` 的 `SKILL_CAPABILITY_ALIASES`）。

**它不接管什么**：provider 协议（鉴权 / WebSocket / 音频解析）属于
`plugins/tts_cosyvoice/`；凭据加密落盘、公网 URL 上传器属于 engine 基座。

**为什么需要 `override_builtin: true`**：内置 `dub_script`（`skill/dub.py`）在启动早期
就注册了，而 `SkillRegistry` 的默认语义是「内置优先、插件不得覆盖」（P1-13）。B3 给它
加了一个**显式**出口：manifest 声明 override_builtin → 门面把冲突策略切成 OVERRIDE →
`SkillRegistry.register(..., allow_override=True)`。旧实例进 `_shadowed`，插件禁用时可恢复。

**编排归属**：切句、逐段合成、确定性 seed、句级/词级时间轴拼接都在本插件的
`dub.py` 里；engine 侧 `VoiceService.synthesize()` 只作为「协议调度基座」被调用
（PRD §15.1.2：engine 保留注册表查询与统一协议调度）。
"""
from __future__ import annotations

from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class TtsFeaturePlugin(BasePlugin):
    """配音工作流（切句 → 合成 → 时间轴）。"""

    def setup(self) -> None:
        """无副作用位——编排在调用时按注册表现状解析 provider。"""

    def contributions(self) -> list[Any]:
        from plugins.tts_feature.dub import DubScriptOrchestrator

        return [
            Contribution(
                ContributionKind.SKILL, "dub_script", DubScriptOrchestrator(),
                meta={"name": "dub_script", "capability": "dub"},
            ),
            # 裁定 C（2026-11-02）：创作页「03 配音与字幕」步骤的内容由本插件贡献。
            # 控件走声明式 layout（宿主 PluginLayoutRenderer 渲染）；`optionsFrom: voices`
            # 表示选项来自宿主白名单数据源（音色列表），插件拿不到任意数据。
            Contribution(
                ContributionKind.UI_SLOT, "create_dub_entry", None,
                meta={
                    "slot": "create.dub",
                    "title": "配音来源",
                    "order": 100,
                    "layout": {
                        "title": "配音来源",
                        "widgets": [
                            {"type": "select", "key": "voice_id", "label": "音色",
                             "optionsFrom": "voices"},
                            {"type": "checkbox", "key": "auto_dub", "label": "自动配音",
                             "defaultValue": True},
                            # 上传配音入口（PRD §15.9 第 10 项）：`file` 控件把 `File`
                            # 回传宿主，**上传执行与回执归宿主**——插件不碰上传 API、
                            # 工作区路径与凭据，也不参与重试/去重/时长探测。
                            {"type": "file", "key": "upload_voice", "label": "上传配音",
                             "accept": ".wav,.mp3,.m4a,.mp4",
                             "placeholder": "选择配音文件（wav / mp3 / m4a）"},
                            {"type": "text", "size": "caption",
                             "content": "上传配音后按实测时长对齐字幕；不上传则用自动配音（需先选音色）。"},
                        ],
                    },
                },
            ),
        ]

