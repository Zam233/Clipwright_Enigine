"""PV 引擎插件（pv_engine）—— 把逐帧渲染引擎接成 agent / 子 agent 可调用的能力。

引擎有两种合法形态（见 ``adapter`` 的双布局说明）：``K:\\video engine`` 这类
``engine/`` 包（``python -m engine.cli``），以及上游 PV 仓库的 ``_build/`` 形态。

本插件贡献（v2）：

- ``pv_engine_doctor``（read）：**反射引擎代码**给出 layouts/kinds/cam/palettes/
  审计规则/CLI 契约（不抄 README，README 有 5 处与代码不符）；
- ``pv_engine_init``（write_local）：脚手架（lyric/slides/overlay）；
- ``pv_engine_probe`` / ``pv_engine_audit`` / ``pv_engine_stills``（v1 保留，审计已结构化）；
- ``pv_engine_render``（costly，带预算闸）；
- ``pv_engine_beats`` / ``pv_engine_align``（costly，拍点 / 逐字对齐）；
- ``pv_engine_timeline_to_project``（write_local）：Clipwright Timeline → project.json；
- 技能 ``pv_engine_review``（体检+审计+抽帧）与 ``pv_engine_authoring``（一条龙）。

**诚实边界**：引擎仓库不含版权素材；``stills``/``render`` 需要先备素材，缺图会如实报错。
引擎目录由操作者配置（插件配置页 / ``CLIPWRIGHT_PV_ENGINE_DIR``），插件只读不写引擎目录。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from clipwright.config import settings
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.skill.base import BaseSkill
from clipwright.tool.base import BaseTool

from .adapter import (
    DEFAULT_AUDIT_TIMEOUT,
    DEFAULT_DOCTOR_TIMEOUT,
    DEFAULT_RENDER_TIMEOUT,
    DEFAULT_STILLS_TIMEOUT,
    LAYOUT_PACKAGE,
    EnginePaths,
    audit_args,
    beats_args,
    init_args,
    parse_audit,
    probe_args,
    render_args,
    resolve_engine,
    run_doctor,
    run_engine,
    stills_args,
)

_DEFAULT_SHOTS = "shots_v20.json"
#: 渲染预算（PRD F5）：默认 120s@30fps 与 30 分钟墙钟
DEFAULT_MAX_FRAMES = 3600
DEFAULT_MAX_WALL_SEC = 1800.0


# --------------------------------------------------------------------- helpers
def _plugin_data() -> Path:
    """插件产物根目录（``PluginData/pv_engine``），**绝对路径**并保证存在。

    必须绝对：引擎是子进程且 cwd=引擎目录，相对路径会被解析到引擎仓库里去
    （实测：``PluginData/pv_engine/x/project.json`` 在引擎侧变成"文件不存在"）。
    """
    root = (Path(str(settings.plugin_data_dir)) / "pv_engine").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_dir(job_id: str = "") -> Path:
    jid = job_id or f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d = _plugin_data() / jid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_of(tool: Any) -> dict[str, Any]:
    """取插件配置：显式 ``config`` 优先，其次 ``config_provider``，都没有返回 {}。"""
    cfg = getattr(tool, "config", None)
    if isinstance(cfg, dict) and cfg:
        return cfg
    provider = getattr(tool, "config_provider", None)
    if callable(provider):
        try:
            cfg = provider()
        except Exception:  # noqa: BLE001 — 配置读取失败按未配置处理
            cfg = None
        if isinstance(cfg, dict):
            return cfg
    return {}


def _engine(tool: Any, explicit: str = "") -> EnginePaths | None:
    cfg = _config_of(tool)
    return resolve_engine(
        explicit=(explicit or None),
        config_dir=str(cfg.get("engine_dir") or "") or None,
    )


def _timeout(tool: Any, key: str, default: float) -> float:
    raw = _config_of(tool).get(key)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(5.0, value)


def engine_available(config: dict[str, Any] | None = None) -> str:
    """诊断用：当前配置下引擎是否可用（空串=可用）。"""
    cfg = config if isinstance(config, dict) else {}
    paths = resolve_engine(config_dir=str(cfg.get("engine_dir") or "") or None)
    if paths is None:
        return "未配置引擎目录（插件配置 engine_dir 或环境变量 CLIPWRIGHT_PV_ENGINE_DIR）"
    return paths.validate()


def _resolve_out(base: Path, value: str) -> Path:
    p = Path(value or "")
    if not p.is_absolute():
        p = base / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _no_engine_result(tool_name: str, paths: EnginePaths | None) -> ToolExecResult:
    reason = paths.validate() if paths is not None else (
        "未配置引擎目录（插件配置 engine_dir 或环境变量 CLIPWRIGHT_PV_ENGINE_DIR）"
    )
    return ToolExecResult(status=ToolStatus.ERROR, tool_name=tool_name, error=reason)


def _glyph_parse(paths: EnginePaths, raw: list[str]) -> dict[str, Any]:
    """把 ``glyph`` 字符串交给引擎的 ``typespec.parse_glyphs`` 解析。

    README 说可以直接写字符串，但 ``project._cue`` 从不调用解析器，
    ``block._chars`` 只吃 dict 列表——写字符串会直接炸（PRD F4b）。
    这里在**落盘前**完成转换，并如实回报哪些动作码引擎没实现。
    """
    if not raw:
        return {"glyphs": [], "actions_implemented": [], "actions_ignored": []}
    if paths.layout == LAYOUT_PACKAGE:
        script = (
            "import json,sys;from engine.typespec import parse_glyphs;"
            "print(json.dumps([parse_glyphs(s) for s in json.loads(sys.argv[1])],ensure_ascii=False))"
        )
        res = run_engine(paths, ["-c", script, json.dumps(raw, ensure_ascii=False)],
                         timeout=60.0)
        if res.get("ok"):
            try:
                parsed = json.loads((res.get("stdout") or "").strip().splitlines()[-1])
                return {"glyphs": parsed, "actions_implemented": _implemented_actions(),
                        "actions_ignored": [], "source": "engine.typespec"}
            except Exception:  # noqa: BLE001 — 落到本地解析
                pass
    return {
        "glyphs": [_fallback_glyph(s) for s in raw],
        "actions_implemented": _fallback_actions(),
        "actions_ignored": [],
        "source": "fallback",
        "warning": "引擎不可用时使用内置最小解析（只支持 字符×缩放↑/↓像素 与 ^旋转）",
    }


def _implemented_actions() -> list[str]:
    # 与 block._chars 的 act 分支一致（slam/stamp/land/lift/wipe/tremble/cut/burn）
    return ["slam", "stamp", "land", "lift", "wipe", "tremble", "cut", "burn"]


def _fallback_actions() -> list[str]:
    return _implemented_actions()


def _fallback_glyph(spec: str) -> list[dict[str, Any]]:
    """最小可用解析：``你×1.2↑4 看×1.0↓2`` → ``[{"ch","s","dy"}]``。"""
    import re

    out: list[dict[str, Any]] = []
    for token in str(spec or "").split():
        if not token:
            continue
        m = re.match(r"^(.)(?:×([\d.]+))?(?:([↑↓])([\d.]+))?(?:\^([-\d.]+))?$", token)
        if not m:
            out.append({"ch": token[:1]})
            continue
        item: dict[str, Any] = {"ch": m.group(1)}
        if m.group(2):
            item["s"] = float(m.group(2))
        if m.group(3):
            dy = float(m.group(4))
            item["dy"] = -dy if m.group(3) == "↑" else dy
        if m.group(5):
            item["r"] = float(m.group(5))
        out.append(item)
    return out


# ------------------------------------------------------------------------ tools
class PvEngineProbeTool(BaseTool):
    """只读：镜头数据体检（数量、时间覆盖、空隙）。"""

    name = "pv_engine_probe"
    capability = "pv_probe"
    agent_callable = True
    description = "体检 PV 引擎的 shots 数据：镜头数、时间覆盖、间隙（只读，可离线）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, shots: str = _DEFAULT_SHOTS, engine_dir: str = "",
                      **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        result = run_engine(paths, probe_args(shots, paths.layout),
                            timeout=_timeout(self, "probe_timeout_sec", DEFAULT_AUDIT_TIMEOUT))
        return ToolExecResult(
            status=ToolStatus.SUCCESS if result.get("ok") else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "engine_root": str(paths.root), "layout": paths.layout,
                    "shots": shots},
            error=None if result.get("ok") else str(result.get("error") or result.get("stderr") or "引擎返回非零"),
        )


class PvEngineAuditTool(BaseTool):
    """只读：跑引擎的硬规则审计（退出码 0 = 全过），并解析成结构化规则。"""

    name = "pv_engine_audit"
    capability = "pv_audit"
    agent_callable = True
    description = "跑引擎规则审计（engine 布局 R01–R11；上游 build 布局规则更多），退出码即结论"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, shots: str = _DEFAULT_SHOTS, video: str = "", scan: bool = False,
                      engine_dir: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        notes: list[str] = []
        if paths.layout == LAYOUT_PACKAGE and (video or scan):
            notes.append("当前为 engine 布局：--video/--scan 只有上游 build 布局支持，已忽略")
        result = run_engine(
            paths, audit_args(shots, video=video, scan=bool(scan), kind=paths.layout),
            timeout=_timeout(self, "audit_timeout_sec", DEFAULT_AUDIT_TIMEOUT),
        )
        parsed = parse_audit(result.get("stdout") or "")
        counts = parsed.get("counts", {}) or {}
        if not result.get("ok") and result.get("exit_code") is None:
            # 没跑起来（超时/目录不对/崩）：这是"错误"，不是"审计不通过"
            verdict = "error"
        elif counts.get("FAIL", 0) or not result.get("ok"):
            verdict = "fail"
        elif counts.get("WARN", 0):
            # 11 条规则里 6 条是 WARN 级：只有 WARN 时退出码为 0，但"通过"
            # 会掩盖"字号过小/末字停留不足/镜头不动"（PRD F9）
            verdict = "pass_with_warnings"
        else:
            verdict = "pass"
        output = {
            **result,
            "engine_root": str(paths.root),
            "layout": paths.layout,
            "shots": shots,
            "verdict": verdict,
            "px_source": "declared",
            "notes": notes,
            **parsed,
        }
        ok = result.get("ok")
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            output=output,
            error=None if ok else (
                "审计未通过（详见 rules / stdout）" if verdict == "fail"
                else str(result.get("error") or "引擎返回非零")
            ),
        )


class PvEngineStillsTool(BaseTool):
    """写工作区：抽静帧供构图复核（产物落 PluginData/pv_engine/<job>/）。"""

    name = "pv_engine_stills"
    capability = "pv_preview"
    agent_callable = True
    description = "用 PV 引擎抽静帧（需要引擎 assets；缺素材会如实报错）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, times: list[float] | None = None, shots: str = _DEFAULT_SHOTS,
                      out_dir: str = "", job_id: str = "", engine_dir: str = "",
                      **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        moments = [float(t) for t in (times or []) if float(t) >= 0]
        if not moments:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="times 不能为空（秒，例如 [20, 90, 158]）")
        job = _job_dir(job_id)
        target = _resolve_out(job, out_dir or "stills")
        result = run_engine(
            paths, stills_args(shots, moments, str(target), paths.layout),
            timeout=_timeout(self, "stills_timeout_sec", DEFAULT_STILLS_TIMEOUT),
        )
        produced = sorted(str(p) for p in target.glob("*.png")) if target.is_dir() else []
        ok = bool(result.get("ok")) and bool(produced)
        rel = target.relative_to(_plugin_data()) if _is_relative_to(target, _plugin_data()) else None
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "engine_root": str(paths.root), "layout": paths.layout,
                    "shots": shots, "job_id": job.name, "out_dir": str(target),
                    "still_files": produced,
                    "preview_urls": [f"/pv-engine/{rel.as_posix()}/{Path(p).name}" for p in produced]
                    if rel else []},
            error=None if ok else "未产出静帧（多半是引擎缺 assets，先准备素材）",
        )


class PvEngineDoctorTool(BaseTool):
    """只读：反射引擎代码，回答"这套引擎会什么"。

    **不做配置门控**：目录填错时它必须能回答"错在哪"，被门控隐藏就失去诊断价值。
    """

    name = "pv_engine_doctor"
    capability = "pv_doctor"
    agent_callable = True
    description = "反射 PV 引擎能力契约（layouts/kinds/相机/调色板/审计规则/CLI），并体检目录"
    dependencies: list[str] = []

    async def execute(self, engine_dir: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="未配置引擎目录（engine_dir 或 CLIPWRIGHT_PV_ENGINE_DIR）；"
                      "仍可查看 known_gaps 了解契约",
                output={"available": False, "known_gaps": _known_gaps()},
            )
        report = run_doctor(paths, timeout=_timeout(self, "doctor_timeout_sec", DEFAULT_DOCTOR_TIMEOUT))
        report["paths"] = paths.describe()
        report["known_gaps"] = _known_gaps()
        ok = bool(report.get("available"))
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            output=report,
            error=None if ok else str(report.get("error") or "引擎不可用"),
        )


class PvEngineInitTool(BaseTool):
    """写工作区：脚手架一个引擎项目。"""

    name = "pv_engine_init"
    capability = "pv_authoring"
    agent_callable = True
    description = "用 PV 引擎脚手架生成 project.json（preset: lyric/slides/overlay）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, dir: str = "", preset: str = "lyric", force: bool = False,
                      engine_dir: str = "", job_id: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        if paths.layout != LAYOUT_PACKAGE:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="init 只有 engine/ 包布局支持（``python -m engine.cli init``）")
        job = _job_dir(job_id)
        target = Path(dir) if dir else job / "project"
        if not target.is_absolute():
            target = job / target
        project_file = target / "project.json"
        if project_file.exists() and not force:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"{project_file} 已存在（force=true 才覆盖）")
        result = run_engine(paths, init_args(str(target), preset, bool(force), paths.layout),
                            timeout=120.0)
        if not result.get("ok"):
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(result.get("stderr") or result.get("error") or "init 失败"),
                                  output={**result, "job_id": job.name})
        data: dict[str, Any] = {}
        if project_file.is_file():
            try:
                data = json.loads(project_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        shots = data.get("shots") or []
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"job_id": job.name, "project_path": str(project_file), "preset": preset,
                    "shots": len(shots),
                    "duration": (data.get("meta") or {}).get("duration"),
                    **result},
        )


class PvEngineRenderTool(BaseTool):
    """整片渲染（costly，带帧数/墙钟预算闸）。"""

    name = "pv_engine_render"
    capability = "pv_render"
    agent_callable = True
    description = "渲染整片 mp4（costly；有帧数与时长预算，超限直接拒绝）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, shots: str = _DEFAULT_SHOTS, out: str = "", codec: str = "nvenc",
                      crf: int = 18, workers: int = 8, start: int = 0, end: int = 0,
                      max_frames: int = DEFAULT_MAX_FRAMES,
                      max_wall_sec: float = DEFAULT_MAX_WALL_SEC,
                      engine_dir: str = "", job_id: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        if paths.layout != LAYOUT_PACKAGE:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="render 只有 engine/ 包布局支持（``python -m engine.cli render``）")
        job = _job_dir(job_id)
        shots_path = Path(shots)
        fps, duration = _project_meta(paths, shots_path)
        planned_frames = int((end or round(duration * fps)) - start)
        if planned_frames <= 0:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="渲染帧数为 0（检查 start/end 与 meta.duration）")
        if planned_frames > int(max_frames):
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=(f"超出预算：本次 {planned_frames} 帧 > max_frames={max_frames}；"
                       "先用 stills 定稿，或显式调大 max_frames"),
                output={"est_frames": planned_frames},
            )
        target = _resolve_out(job, out or "film.mp4")
        wall = min(float(max_wall_sec), DEFAULT_RENDER_TIMEOUT)
        result = run_engine(
            paths, render_args(shots, str(target), codec=codec, crf=crf, workers=workers,
                               start=start, end=end, kind=paths.layout),
            timeout=wall,
        )
        ok = bool(result.get("ok")) and target.is_file()
        rel = target.relative_to(_plugin_data()) if _is_relative_to(target, _plugin_data()) else None
        asset: dict[str, Any] | None = None
        asset_error = ""
        if ok:
            # F8 产物回流：登记进素材库（sha256 去重、缩略图、used_count 由 AssetManager 负责）
            try:
                from clipwright.services.asset_manager import AssetManager

                info = await AssetManager().import_file(target)
                asset = info.to_dict() if info is not None else None
                if asset and not asset.get("asset_id"):
                    asset_error = str(asset.get("error") or "登记失败")
                    asset = None
            except Exception as exc:  # noqa: BLE001 — 登记失败不影响出片结果
                asset_error = str(exc)[:200]
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "job_id": job.name, "output_path": str(target),
                    "url": f"/pv-engine/{rel.as_posix()}" if rel else "",
                    "est_frames": planned_frames, "wall_budget_sec": wall,
                    "asset": asset, "asset_error": asset_error,
                    "cost_usd": None},
            output_path=str(target) if ok else None,
            error=None if ok else str(result.get("error") or result.get("stderr") or "渲染失败"),
        )


class PvEngineBeatsTool(BaseTool):
    """拍点网格（costly：解码整段音频）。"""

    name = "pv_engine_beats"
    capability = "pv_beats"
    agent_callable = True
    description = "从音频估计拍点网格（bpm/period/beats），costly"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, audio: str, out: str = "", engine_dir: str = "",
                      job_id: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        if paths.layout != LAYOUT_PACKAGE:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="beats 只有 engine/ 包布局支持")
        if not Path(audio).is_file():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"音频不存在：{audio}")
        job = _job_dir(job_id)
        target = _resolve_out(job, out or "beats.json")
        result = run_engine(paths, beats_args(audio, str(target), paths.layout), timeout=600.0)
        data: dict[str, Any] = {}
        if target.is_file():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        return ToolExecResult(
            status=ToolStatus.SUCCESS if result.get("ok") and data else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "job_id": job.name, "out_path": str(target),
                    "bpm": data.get("bpm"), "period": data.get("period"),
                    "beats": (data.get("beats") or [])[:2000]},
            error=None if result.get("ok") and data else str(result.get("error") or result.get("stderr") or "beats 失败"),
        )


class PvEngineAlignTool(BaseTool):
    """逐字对齐（costly：whisper）。"""

    name = "pv_engine_align"
    capability = "pv_align"
    agent_callable = True
    description = "把已有文案与音频逐字对齐（需要 whisper，缺依赖如实报错）"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, text: str, audio: str, language: str = "", model: str = "small",
                      engine_dir: str = "", **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        if paths.layout != LAYOUT_PACKAGE:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="align 只有 engine/ 包布局支持")
        if not text.strip():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="text 不能为空")
        if not Path(audio).is_file():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"音频不存在：{audio}")
        script = (
            "import json,sys;from engine.audio import align;"
            "a=json.loads(sys.argv[1]);print(json.dumps(align(a['text'],a['audio'],"
            "language=a.get('language') or None,model=a.get('model') or 'small')))"
        )
        args = {"text": text, "audio": audio, "language": language, "model": model}
        result = run_engine(paths, ["-c", script, json.dumps(args, ensure_ascii=False)], timeout=1800.0)
        words: list[Any] = []
        if result.get("ok"):
            try:
                words = json.loads((result.get("stdout") or "").strip().splitlines()[-1])
            except Exception:  # noqa: BLE001
                words = []
        return ToolExecResult(
            status=ToolStatus.SUCCESS if result.get("ok") else ToolStatus.ERROR,
            tool_name=self.name,
            output={**result, "words": words, "count": len(words)},
            error=None if result.get("ok") else str(result.get("stderr") or result.get("error") or "align 失败"),
        )


class PvEngineTimelineToProjectTool(BaseTool):
    """Timeline → project.json（单向桥；引擎侧逐帧渲染的入口）。"""

    name = "pv_engine_timeline_to_project"
    capability = "pv_timeline"
    agent_callable = True
    description = "把 Clipwright Timeline（caption/text clip）转成 PV 引擎 project.json"
    dependencies: list[str] = []

    def is_configured(self) -> bool:
        return engine_available(_config_of(self)) == ""

    async def execute(self, timeline: dict[str, Any] | None = None,
                      clips: list[dict[str, Any]] | None = None,
                      out: str = "", audio: str = "", engine_dir: str = "",
                      job_id: str = "", default_bg: str = "proc:hatch",
                      **kwargs: Any) -> ToolExecResult:
        paths = _engine(self, engine_dir)
        if paths is None or paths.validate():
            return _no_engine_result(self.name, paths)
        tl = timeline or {}
        cands = _collect_caption_clips(tl, clips or [])
        if not cands:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="timeline 里没有 caption/text clip（或都未启用）")
        project, warnings = _build_project(tl, cands, audio=audio, default_bg=default_bg,
                                           paths=paths)
        job = _job_dir(job_id)
        target = _resolve_out(job, out or "project.json")
        target.write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"job_id": job.name, "project_path": str(target), "shots": len(project["shots"]),
                    "cues": len(cands), "warnings": warnings,
                    "project": project},
        )


# ------------------------------------------------------------------ timeline map
def _collect_caption_clips(timeline: dict[str, Any], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 Timeline.tracks 或裸 clip 列表里取「要交给引擎」的文字片段。"""
    pool: list[dict[str, Any]] = list(extra or [])
    for track in (timeline.get("tracks") or []):
        for clip in (track.get("clips") or []):
            item = dict(clip)
            item.setdefault("_track_id", track.get("id", ""))
            pool.append(item)
    out: list[dict[str, Any]] = []
    for clip in pool:
        kind = str(clip.get("kind") or "")
        if kind not in ("caption", "text"):
            continue
        if clip.get("enabled") is False:
            continue
        text = str(clip.get("text") or "").strip()
        if not text:
            continue
        pv = ((clip.get("metadata") or {}).get("pv") or {})
        if pv.get("enabled") is False:
            continue
        start = float(clip.get("start_sec") or 0.0)
        dur = float(clip.get("duration_sec") or 0.0)
        if dur <= 0:
            continue
        out.append({"clip": clip, "pv": pv, "text": text, "t0": start, "t1": start + dur})
    out.sort(key=lambda c: (c["t0"], c["t1"]))
    return out


_LAYOUT_BY_POSITION = {
    ("bottom", "center"): "low_center",
    ("bottom", "left"): "low_left",
    ("bottom", "right"): "low_right",
    ("top", "center"): "up_center",
    ("top", "left"): "up_left",
    ("top", "right"): "up_right",
    ("center", "center"): "center",
}


def _layout_for(clip: dict[str, Any], pv: dict[str, Any]) -> str:
    if pv.get("layout"):
        return str(pv["layout"])
    meta = clip.get("metadata") or {}
    pos = str(meta.get("position") or "bottom").lower()
    align = str(clip.get("text_align") or "center").lower()
    return _LAYOUT_BY_POSITION.get((pos, align), "low_center")


def _cue_from(clip_entry: dict[str, Any]) -> dict[str, Any]:
    clip, pv = clip_entry["clip"], clip_entry["pv"]
    cue: dict[str, Any] = {
        "text": clip_entry["text"],
        "t0": round(clip_entry["t0"], 3),
        "t1": round(clip_entry["t1"], 3),
        "layout": _layout_for(clip, pv),
        "kind": str(pv.get("kind") or "band"),
        "band": float(pv.get("band") or 0.78),
        "stagger": float(pv.get("stagger") or 0.0),
        "tracking": float(pv.get("tracking") or 0.02),
        "glow": float(pv.get("glow") or 0.45),
    }
    if clip.get("font_size"):
        cue["px"] = int(float(clip["font_size"]))
    if clip.get("font_color"):
        cue["color"] = str(clip["font_color"])
    if pv.get("vertical"):
        cue["vertical"] = True
    return cue


def _build_project(timeline: dict[str, Any], entries: list[dict[str, Any]], *,
                   audio: str, default_bg: str, paths: EnginePaths) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    width = int(timeline.get("width") or 1920)
    height = int(timeline.get("height") or 1080)
    fps = float(timeline.get("fps") or 30.0)

    raw_glyphs = [str(e["pv"]["glyph"]) for e in entries if e["pv"].get("glyph")]
    glyph_report: dict[str, Any] = {}
    if raw_glyphs:
        glyph_report = _glyph_parse(paths, raw_glyphs)
        if glyph_report.get("warning"):
            warnings.append(glyph_report["warning"])

    glyph_iter = iter(glyph_report.get("glyphs") or [])
    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in entries:
        cue = _cue_from(entry)
        if entry["pv"].get("glyph"):
            parsed = next(glyph_iter, [])
            if parsed:
                cue["glyph"] = parsed
        prepared.append((cue, entry["pv"]))
    cues = [cue for cue, _ in prepared]

    # 分组成"不重叠的 cue 群"，再让镜头首尾相接覆盖整条时间轴（R01/R07）
    groups: list[dict[str, Any]] = []
    for cue, pv in prepared:
        if groups and cue["t0"] <= groups[-1]["t1"] + 0.01:
            groups[-1]["cues"].append(cue)
            groups[-1]["t1"] = max(groups[-1]["t1"], cue["t1"])
        else:
            groups.append({"t0": cue["t0"], "t1": cue["t1"], "cues": [cue], "pv": pv})

    duration = max([float(timeline.get("duration_sec") or 0.0)] + [c["t1"] for c in cues])
    shots: list[dict[str, Any]] = []
    prev_end = 0.0
    for i, group in enumerate(groups):
        t0 = prev_end
        t1 = group["t1"] if i < len(groups) - 1 else max(duration, group["t1"])
        if t1 <= t0:
            t1 = t0 + 0.2
        pv0 = group.get("pv") or {}
        shots.append({
            "t0": round(t0, 3),
            "t1": round(t1, 3),
            "bg": str(pv0.get("bg") or default_bg),
            "palette": str(pv0.get("palette") or "sepia"),
            "camera": str(pv0.get("camera") or "push_soft"),
            "text": group["cues"],
        })
        prev_end = t1

    project = {
        "meta": {"title": str(timeline.get("id") or "clipwright"), "width": width,
                 "height": height, "fps": fps, "safe": max(16, int(min(width, height) * 0.05)),
                 "duration": round(duration, 3), "out": "out/film.mp4"},
        "audio": audio or timeline.get("audio") or None,
        "assets": {"root": "assets"},
        "defaults": {"palette": "sepia", "post": {"scan": 0.25}},
        "shots": shots,
    }
    if glyph_report.get("actions_ignored"):
        warnings.append("引擎未实现的动作码：" + ", ".join(glyph_report["actions_ignored"]))
    # 时间越界/重叠在这里就说清楚（不要留给审计去猜）
    for entry in entries:
        if entry["t1"] > duration + 0.02:
            warnings.append(f"clip 结束时间 {entry['t1']} 超出 timeline.duration {duration}")
    return project, warnings


def _project_meta(paths: EnginePaths, shots: Path) -> tuple[float, float]:
    """从 project.json 读 fps/duration（读不到时退保守默认）。"""
    p = shots if shots.is_absolute() else paths.root / shots
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        meta = data.get("meta") or {}
        fps = float(meta.get("fps") or 24.0)
        duration = float(meta.get("duration") or max(
            [float(s.get("t1") or 0) for s in (data.get("shots") or [])] or [0.0]))
        return fps, duration
    except Exception:  # noqa: BLE001
        return 24.0, 0.0


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:  # noqa: BLE001
        return False


def _known_gaps() -> list[dict[str, str]]:
    """引擎侧已知缺陷/死字段（与 docs/subtitle-bug-report-2026-10-23.md 同源）。"""
    return [
        {"id": "S01", "summary": "标点会让逐字时间（word_times）整体错位一个字"},
        {"id": "S02", "summary": "英文空格被 strip_punct 删掉（hello world → helloworld）"},
        {"id": "S03", "summary": "没有按像素换行，长句只能缩到 36px"},
        {"id": "S04", "summary": "审计的 px 与真实渲染 px 不是同一个数（可能假 WARN）"},
        {"id": "D1", "summary": "README 的 layout: fill_low 在代码里不存在"},
        {"id": "D2", "summary": "vertical 是 cue 布尔，不是 kind"},
        {"id": "D3", "summary": "glyph 字符串需先解析成 dict 列表（本插件已代做）"},
        {"id": "D4", "summary": "trans_in / tr_out / motion 等字段 expand_shot 不拷贝（不可达）"},
    ]


# ----------------------------------------------------------------------- skills
class PvEngineReviewSkill(BaseSkill):
    """把「体检 → 审计 → 抽帧」串成一步，供主 agent / 子 agent 直接调用。"""

    name = "pv_engine_review"
    description = "对一支 PV 跑数据体检 + 规则审计，可选抽静帧复核"
    required_tools = ["pv_engine_probe", "pv_engine_audit"]

    async def execute(self, shots: str = _DEFAULT_SHOTS, stills_times: list[float] | None = None,
                      engine_dir: str = "", **kwargs: Any) -> SkillExecResult:
        probe = await self._run_tool("pv_engine_probe", shots=shots, engine_dir=engine_dir)
        audit = await self._run_tool("pv_engine_audit", shots=shots, engine_dir=engine_dir)
        stills: dict[str, Any] | None = None
        if stills_times:
            stills = await self._run_tool("pv_engine_stills", times=stills_times,
                                          shots=shots, engine_dir=engine_dir)
        audit_out = (audit.get("output") or {}) if isinstance(audit, dict) else {}
        ok = audit_out.get("verdict") in ("pass", "pass_with_warnings") or bool(audit_out.get("ok"))
        summary = {
            "verdict": audit_out.get("verdict"),
            "counts": audit_out.get("counts"),
            "shot_count": (probe.get("output") or {}).get("shots"),
        }
        return SkillExecResult(
            status=SkillStatus.SUCCESS if ok else SkillStatus.FAILED,
            skill_name=self.name,
            output={"probe": probe, "audit": audit, "stills": stills, "summary": summary},
            error=None if ok else "审计未通过或引擎不可用",
        )


class PvEngineAuthoringSkill(BaseSkill):
    """一条龙：doctor → timeline_to_project（或 init）→ audit（→ stills）。"""

    name = "pv_engine_authoring"
    description = "从 Timeline 生成 PV 项目并审计：doctor → project → audit（可选 stills）"
    required_tools = ["pv_engine_doctor", "pv_engine_audit"]

    async def execute(self, timeline: dict[str, Any] | None = None,
                      shots: str = "", preset: str = "", stills_times: list[float] | None = None,
                      engine_dir: str = "", job_id: str = "", **kwargs: Any) -> SkillExecResult:
        doctor = await self._run_tool("pv_engine_doctor", engine_dir=engine_dir)
        if not ((doctor.get("output") or {}).get("available")):
            return SkillExecResult(status=SkillStatus.FAILED, skill_name=self.name,
                                   output={"doctor": doctor},
                                   error="引擎不可用（先修 engine_dir / 安装依赖）")
        project_path = shots
        steps: dict[str, Any] = {"doctor": doctor}
        if timeline:
            made = await self._run_tool("pv_engine_timeline_to_project", timeline=timeline,
                                        engine_dir=engine_dir, job_id=job_id)
            steps["project"] = made
            project_path = str((made.get("output") or {}).get("project_path") or "")
            if not project_path:
                return SkillExecResult(status=SkillStatus.FAILED, skill_name=self.name,
                                       output=steps, error="Timeline 转换失败")
        elif preset:
            created = await self._run_tool("pv_engine_init", preset=preset,
                                           engine_dir=engine_dir, job_id=job_id)
            steps["init"] = created
            project_path = str((created.get("output") or {}).get("project_path") or "")
        if not project_path:
            return SkillExecResult(status=SkillStatus.FAILED, skill_name=self.name,
                                   output=steps, error="缺少 timeline 或 preset（或 shots 路径）")
        audit = await self._run_tool("pv_engine_audit", shots=project_path, engine_dir=engine_dir)
        steps["audit"] = audit
        stills = None
        if stills_times:
            stills = await self._run_tool("pv_engine_stills", times=stills_times,
                                          shots=project_path, engine_dir=engine_dir,
                                          job_id=job_id)
            steps["stills"] = stills
        verdict = (audit.get("output") or {}).get("verdict")
        return SkillExecResult(
            status=SkillStatus.SUCCESS if verdict in ("pass", "pass_with_warnings") else SkillStatus.FAILED,
            skill_name=self.name,
            output={**steps, "project_path": project_path,
                    "summary": {"verdict": verdict,
                                "counts": (audit.get("output") or {}).get("counts")}},
            error=None if verdict in ("pass", "pass_with_warnings") else "审计未通过",
        )


# ----------------------------------------------------------------------- plugin
class PvEnginePlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="pv_engine",
        name="PV Engine（逐帧渲染引擎）",
        version="2.0.0",
        kind=PluginKind.CAPABILITY,
        description="把逐帧纯函数渲染引擎接成 agent 可调用的工具与技能（双布局/透传审计/带预算渲染）",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        return [
            Contribution(ContributionKind.TOOL, "pv_engine_doctor", PvEngineDoctorTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_init", PvEngineInitTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_probe", PvEngineProbeTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_audit", PvEngineAuditTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_stills", PvEngineStillsTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_render", PvEngineRenderTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_beats", PvEngineBeatsTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_align", PvEngineAlignTool()),
            Contribution(ContributionKind.TOOL, "pv_engine_timeline_to_project",
                         PvEngineTimelineToProjectTool()),
            Contribution(ContributionKind.SKILL, "pv_engine_review", PvEngineReviewSkill()),
            Contribution(ContributionKind.SKILL, "pv_engine_authoring", PvEngineAuthoringSkill()),
        ]


__all__ = [
    "PvEngineAlignTool",
    "PvEngineAuditTool",
    "PvEngineAuthoringSkill",
    "PvEngineBeatsTool",
    "PvEngineDoctorTool",
    "PvEngineInitTool",
    "PvEnginePlugin",
    "PvEngineProbeTool",
    "PvEngineRenderTool",
    "PvEngineReviewSkill",
    "PvEngineStillsTool",
    "PvEngineTimelineToProjectTool",
    "engine_available",
]
