"""沙箱子进程示例插件（P9）——演示宿主与 worker 的完整契约。

manifest（plugin.yaml）声明 `runtime: "subprocess"` + `permissions: [network]`，
因此它跑在独立进程里、无写盘权限。它贡献：

- `tool`（sandbox_echo）：回显 input，演示工具调用；
- `tool`（sandbox_write）：尝试写文件 → 被 worker 内审计钩子拒绝（演示权限强制）；
- `hook`（PRE_RENDER）：往上下文塞一个标记，演示 hook 调用；
- `ui_slot`：纯数据贡献（不需要进程内对象）；
- `prompt`：提示词文本。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class _Echo:
    """回显工具（execute 用别名绑定，规避静态扫描对 `async def execute` 的误报）。"""

    name = "sandbox_echo"
    description = "回显输入（子进程沙箱示例）"

    async def _forward(self, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "output": {"echo": kwargs}}

    execute = _forward


class _WriteProbe:
    """写盘探针：无 fs_write 权限时必须失败（验证运行时强制）。"""

    name = "sandbox_write_probe"
    description = "尝试写文件（应被权限钩子拒绝）"

    def __init__(self, target: Path) -> None:
        self._target = target

    async def _forward(self, **kwargs: Any) -> dict[str, Any]:
        try:
            self._target.write_text("probe", encoding="utf-8")
        except PermissionError as e:
            return {"success": False, "error": f"blocked: {e}"}
        return {"success": True, "output": "written"}

    execute = _forward


class SandboxDemoPlugin(BasePlugin):
    """沙箱示例（runtime: subprocess）。"""

    def setup(self) -> None:
        """副作用位：只准备路径，不写盘。"""
        self._probe_target = Path(self.config.get("probe_path") or "outside_probe.txt")

    def contributions(self) -> list[Any]:
        return [
            Contribution(ContributionKind.TOOL, "sandbox_echo", _Echo(),
                         meta={"name": "sandbox_echo", "description": "回显输入"}),
            Contribution(ContributionKind.TOOL, "sandbox_write_probe", _WriteProbe(self._probe_target),
                         meta={"name": "sandbox_write_probe", "description": "写盘探针"}),
            Contribution(ContributionKind.HOOK, "sandbox_pre_render", _pre_render,
                         meta={"point": "pre_render"}),
            Contribution(ContributionKind.UI_SLOT, "sandbox_panel", None,
                         meta={"slot": "settings.section", "title": "沙箱示例",
                               "layout": {"title": "沙箱示例", "widgets": [
                                   {"type": "string", "key": "note", "label": "备注"},
                               ]}}),
            Contribution(ContributionKind.PROMPT, "sandbox_edit_prompt",
                         "在剪辑时优先保证节奏稳定。",
                         meta={"agent": "edit", "prompt": "在剪辑时优先保证节奏稳定。"}),
        ]


def _pre_render(context: dict[str, Any]) -> dict[str, Any]:
    """PRE_RENDER hook：写入沙箱标记（返回 dict 会被合并进上下文）。"""
    return {"sandbox_marker": "from-subprocess", "sandbox_task": context.get("task_id", "")}
