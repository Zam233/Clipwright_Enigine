"""PV 引擎插件（pv_engine）：把 eternal-questioning-pv 的引擎接成可被 agent 调用的能力。

引擎仓库：https://github.com/Zam233/eternal-questioning-pv
（逐帧纯函数渲染的歌词 PV 引擎：2.5D 摄像机 / 逐字排版 / 场域翻转 / 后期节点 / 规则审计）

本插件贡献三件东西：

- ``pv_engine_probe``（read）：读 shots 数据，报镜头数与时间覆盖、列出空隙；
- ``pv_engine_audit``（read）：跑引擎的 30+ 条硬规则审计，**退出码即结论**；
- ``pv_engine_stills``（write_local）：抽静帧供构图复核（写工作区）。

再加一个 ``pv_engine_review`` 技能把三步串起来，方便主 agent / 子 agent 一次调用。

**诚实边界**：引擎仓库里不含版权素材（MET 公版版画与 AI 人物层都在 `.gitignore`）。
所以 `probe`/`audit` 可离线跑；`stills`/`frames` 需要先把素材下载/生成到引擎的
`assets/` 与 `_build/assets/ai/`，否则引擎会报缺图——本插件如实透传那个错误，
不会伪造"渲染成功"。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.skill.base import BaseSkill
from clipwright.tool.base import BaseTool

from .adapter import (
    DEFAULT_AUDIT_TIMEOUT,
    DEFAULT_STILLS_TIMEOUT,
    audit_args,
    probe_args,
    resolve_engine,
    run_engine,
    stills_args,
)

_DEFAULT_SHOTS = "shots_v20.json"


def _engine(config: dict[str, Any] | None, explicit: str = ""):
    cfg = config or {}
    return resolve_engine(
        explicit=explicit or None,
        config_dir=str(cfg.get("engine_dir") or "") or None,
    )


def _timeout(config: dict[str, Any] | None, key: str, default: float) -> float:
    raw = (config or {}).get(key)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(5.0, value)


class PvEngineProbeTool(BaseTool):
    """只读：镜头数据体检（数量、时间覆盖、空隙）。"""

    name = "pv_engine_probe"
    agent_callable = True
    description = "体检 PV 引擎的 shots 数据：镜头数、时间覆盖、间隙（只读，可离线）"
    # 引擎是"一个目录"而不是可执行文件，不能用 dependencies（它按 shutil.which 查）
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        """未配置引擎目录 → 不进阶段 agent 的工具面（避免白烧 token）。"""
        return engine_available() == ""

    async def execute(
        self,
        shots: str = _DEFAULT_SHOTS,
        engine_dir: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        paths = _engine(getattr(self, "config", None), engine_dir)
        if paths is None:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error=("未配置 PV 引擎目录：请在插件配置里填 engine_dir，"
                       "或设环境变量 CLIPWRIGHT_PV_ENGINE_DIR 指向 eternal-questioning-pv 仓库根目录"),
            )
        result = run_engine(
            paths, probe_args(shots),
            timeout=_timeout(getattr(self, "config", None), "probe_timeout_sec", DEFAULT_AUDIT_TIMEOUT),
        )
        return ToolExecResult(
            status=ToolStatus.SUCCESS if result.get("ok") else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "engine_root": str(paths.root), "shots": shots},
            error=None if result.get("ok") else str(result.get("error") or result.get("stderr") or "引擎返回非零"),
        )


class PvEngineAuditTool(BaseTool):
    """只读：跑引擎的硬规则审计（退出码 0 = 全过）。"""

    name = "pv_engine_audit"
    agent_callable = True
    description = "跑 eternal-questioning-pv 的规则审计（R01–R36）；退出码即结论"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available() == ""

    async def execute(
        self,
        shots: str = _DEFAULT_SHOTS,
        video: str = "",
        scan: bool = False,
        engine_dir: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        paths = _engine(getattr(self, "config", None), engine_dir)
        if paths is None:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="未配置 PV 引擎目录（engine_dir 或 CLIPWRIGHT_PV_ENGINE_DIR）",
            )
        result = run_engine(
            paths, audit_args(shots, video=video, scan=bool(scan)),
            timeout=_timeout(getattr(self, "config", None), "audit_timeout_sec", DEFAULT_AUDIT_TIMEOUT),
        )
        # 审计结论以退出码表达：0=全过，非 0=有 FAIL（不是"工具坏了"，如实区分）
        return ToolExecResult(
            status=ToolStatus.SUCCESS if result.get("ok") else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "engine_root": str(paths.root), "shots": shots,
                    "verdict": "pass" if result.get("ok") else "fail"},
            error=None if result.get("ok") else "审计未通过（详见 stdout/stderr）",
        )


class PvEngineStillsTool(BaseTool):
    """写工作区：抽静帧供构图复核。"""

    name = "pv_engine_stills"
    agent_callable = True
    description = "用 PV 引擎抽静帧（需要引擎 assets；缺素材会如实报错）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available() == ""

    async def execute(
        self,
        times: list[float] | None = None,
        shots: str = _DEFAULT_SHOTS,
        out_dir: str = "pv_stills",
        engine_dir: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        paths = _engine(getattr(self, "config", None), engine_dir)
        if paths is None:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="未配置 PV 引擎目录（engine_dir 或 CLIPWRIGHT_PV_ENGINE_DIR）",
            )
        moments = [float(t) for t in (times or []) if float(t) >= 0]
        if not moments:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="times 不能为空（秒，例如 [20, 90, 158]）",
            )
        target = Path(out_dir)
        if not target.is_absolute():
            target = paths.build_dir / target
        result = run_engine(
            paths, stills_args(shots, moments, str(target)),
            timeout=_timeout(getattr(self, "config", None), "stills_timeout_sec", DEFAULT_STILLS_TIMEOUT),
        )
        produced = sorted(str(p.name) for p in target.glob("still_*.png")) if target.is_dir() else []
        ok = bool(result.get("ok")) and bool(produced)
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "engine_root": str(paths.root), "shots": shots,
                    "out_dir": str(target), "still_files": produced},
            error=None if ok else "未产出静帧（多半是引擎缺 assets，先跑 fetch_met/gen 准备素材）",
        )


class PvEngineReviewSkill(BaseSkill):
    """把「体检 → 审计 → 抽帧」串成一步，供主 agent / 子 agent 直接调用。"""

    name = "pv_engine_review"
    description = "对一支 PV 跑数据体检 + 规则审计，可选抽静帧复核"
    required_tools = ["pv_engine_probe", "pv_engine_audit"]

    async def execute(
        self,
        shots: str = _DEFAULT_SHOTS,
        stills_times: list[float] | None = None,
        engine_dir: str = "",
        **kwargs: Any,
    ) -> SkillExecResult:
        probe = await self._run_tool("pv_engine_probe", shots=shots, engine_dir=engine_dir)
        audit = await self._run_tool("pv_engine_audit", shots=shots, engine_dir=engine_dir)
        stills: dict[str, Any] | None = None
        if stills_times:
            stills = await self._run_tool(
                "pv_engine_stills", times=stills_times, shots=shots, engine_dir=engine_dir,
            )
        audit_ok = bool(((audit.get("output") or {}) if isinstance(audit, dict) else {}).get("ok"))
        status = SkillStatus.SUCCESS if audit_ok else SkillStatus.FAILED
        return SkillExecResult(
            status=status,
            skill_name=self.name,
            output={"probe": probe, "audit": audit, "stills": stills},
            error=None if audit_ok else "审计未通过或引擎不可用",
        )


class PvEnginePlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="pv_engine",
        name="PV Engine (eternal-questioning-pv)",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="把逐帧纯函数渲染的 PV 引擎接成 agent 可调用的工具与技能",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        return [
            Contribution(ContributionKind.TOOL, "pv_engine_probe", PvEngineProbeTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_audit", PvEngineAuditTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_stills", PvEngineStillsTool()),
            Contribution(ContributionKind.SKILL, "pv_engine_review", PvEngineReviewSkill()),
        ]


def engine_available() -> str:
    """诊断用：当前配置下引擎是否可用（空串=可用）。"""
    paths = resolve_engine(
        config_dir=os.environ.get("CLIPWRIGHT_PV_ENGINE_DIR"),
    )
    if paths is None:
        return "未配置 CLIPWRIGHT_PV_ENGINE_DIR"
    return paths.validate()


__all__ = [
    "PvEngineAuditTool",
    "PvEnginePlugin",
    "PvEngineProbeTool",
    "PvEngineReviewSkill",
    "PvEngineStillsTool",
    "engine_available",
]
