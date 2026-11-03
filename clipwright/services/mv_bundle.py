"""MV 工程包载入与交付打包（PRD C12 / R-18）。

工程包目录约定（``examples/mv_poc/`` 等）：

```
mv.json          # 根：歌曲信息、类型、片段表、门禁
cues.json        # 时间唯一真相（grid + items，每项标 measured/init）
lyrics.json      # 歌词行/词/音节（只存 text_ref）
chat_script.json # 可选：聊天界面台词与状态切换
plan.json        # 可选：PlanDocument（plan-first 产物）
brief.json       # 可选：创意简报
report.json      # 可选：渲染/门禁报告（由渲染脚本产出）
```

``pack_dist`` 把上述产物收口成交付六件套（``docs/mv-pv-prd`` §C12）：

```
dist/<mv_id>/
  preview_1080p60.mp4   （若存在则复制）
  storyboard.md
  cues.json
  plan.md / brief.md
  report.json
  selftest.md
  assets_gap.md
  CREDITS.md
```

本模块只做**读写与校验**，不做渲染；缺文件时明确列出来（不许静默生成空壳）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.category_spec import PlanDocument
from clipwright.services.mv_cues import CuesDoc, load_cues
from clipwright.services.plan_renderer import render_brief_markdown, render_plan_markdown

#: 工程包必需文件。
REQUIRED_FILES: tuple[str, ...] = ("mv.json", "cues.json")
#: 交付六件套（预览视频可选）。
DIST_FILES: tuple[str, ...] = (
    "preview.mp4",
    "storyboard.md",
    "cues.json",
    "plan.md",
    "brief.md",
    "report.json",
    "selftest.md",
    "assets_gap.md",
    "CREDITS.md",
)


@dataclass
class MvBundle:
    """一个已载入的 MV 工程包。"""

    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    cues: Optional[CuesDoc] = None
    lyrics: dict[str, Any] = field(default_factory=dict)
    chat_script: dict[str, Any] = field(default_factory=dict)
    plan: Optional[PlanDocument] = None
    brief: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def mv_id(self) -> str:
        return str(self.manifest.get("id") or self.root.name)

    @property
    def duration_sec(self) -> float:
        song = self.manifest.get("song") or {}
        return float(song.get("duration_sec") or self.manifest.get("duration_sec") or 0.0)

    def gates(self) -> dict[str, bool]:
        raw = self.manifest.get("gates") or {}
        return {
            "determinism": bool(raw.get("determinism", True)),
            "beat": bool(raw.get("beat", True)),
            "flash": bool(raw.get("flash", True)),
        }


def load_bundle(root: str | Path) -> MvBundle:
    """载入工程包；缺必需文件时记入 ``missing``（不抛异常，便于诊断）。"""
    directory = Path(root)
    if not directory.exists():
        raise FileNotFoundError(directory)
    bundle = MvBundle(root=directory)

    for name in REQUIRED_FILES:
        if not (directory / name).exists():
            bundle.missing.append(name)

    bundle.manifest = _read_json(directory / "mv.json", {})
    bundle.lyrics = _read_json(directory / "lyrics.json", {})
    bundle.chat_script = _read_json(directory / "chat_script.json", {})
    bundle.brief = _read_json(directory / "brief.json", {})
    bundle.report = _read_json(directory / "report.json", {})

    cues_path = directory / "cues.json"
    if cues_path.exists():
        try:
            bundle.cues = load_cues(cues_path)
        except Exception:  # noqa: BLE001 - 坏 cues 记入 missing，后续按无 cues 处理
            bundle.missing.append("cues.json (parse)")

    plan_path = directory / "plan.json"
    if plan_path.exists():
        try:
            bundle.plan = PlanDocument(**_read_json(plan_path, {}))
        except Exception:  # noqa: BLE001
            bundle.missing.append("plan.json (parse)")
    return bundle


def validate_bundle(bundle: MvBundle) -> list[dict[str, str]]:
    """结构校验：返回问题列表（空 = 通过）。"""
    issues: list[dict[str, str]] = []
    for name in bundle.missing:
        issues.append({"code": "bundle_missing_file", "where": name, "message": f"缺少 {name}"})

    segments = bundle.manifest.get("segments") or []
    if not segments:
        issues.append({"code": "bundle_no_segments", "where": "mv.json", "message": "缺少 segments"})
    else:
        prev_end = 0.0
        for segment in segments:
            try:
                t0, t1 = float(segment.get("t0", 0)), float(segment.get("t1", 0))
            except (TypeError, ValueError):
                issues.append({"code": "bundle_bad_segment", "where": str(segment), "message": "片段时间为非数字"})
                continue
            if t1 <= t0:
                issues.append({"code": "bundle_bad_segment", "where": str(segment.get("id")), "message": "t1 不大于 t0"})
            if t0 > prev_end + 1e-6:
                issues.append({
                    "code": "bundle_segment_gap",
                    "where": str(segment.get("id")),
                    "message": f"与上一段之间有空洞：{prev_end} → {t0}",
                })
            prev_end = max(prev_end, t1)

    if bundle.cues is not None and bundle.cues.grid.beat_sec <= 0:
        issues.append({"code": "bundle_no_grid", "where": "cues.json", "message": "cues.grid.bpm 为 0"})
    return issues


def render_storyboard(bundle: MvBundle) -> str:
    """由片段表 + cues 生成分镜表（确定性文本）。"""
    lines = [f"# 分镜表 — {bundle.mv_id}", ""]
    segments = bundle.manifest.get("segments") or []
    if not segments:
        lines.append("_（无片段）_")
        return "\n".join(lines) + "\n"
    lines.append("| # | 片段 | 时间 | 画面类型 | 场景 |")
    lines.append("|---|------|------|----------|------|")
    for index, segment in enumerate(segments, 1):
        lines.append(
            f"| {index} | {segment.get('id', '')} | "
            f"{float(segment.get('t0', 0)):.2f}–{float(segment.get('t1', 0)):.2f}s | "
            f"{segment.get('kind', '')} | {segment.get('scene', '')} |"
        )
    lines.append("")
    if bundle.cues is not None and bundle.cues.items:
        lines.append("## 卡点（cues）")
        lines.append("")
        lines.append("| 名称 | 时间 | 来源 | 吸附 |")
        lines.append("|------|------|------|------|")
        for item in bundle.cues.items:
            lines.append(f"| {item.name} | {item.t:.3f}s | {item.source} | {item.snapped} |")
        lines.append("")
        init_count = bundle.cues.init_count()
        lines.append(f"共 {len(bundle.cues.items)} 项，其中 `init`（待实测确认）{init_count} 项。")
    return "\n".join(lines) + "\n"


def render_selftest(bundle: MvBundle) -> str:
    """自查清单（六件套之一）：报告里的数字 + 还剩哪些人工项。"""
    report = bundle.report or {}
    lines = [f"# 自检 — {bundle.mv_id}", ""]
    rows = [
        ("确定性（逐帧哈希）", report.get("deterministic")),
        ("拍点命中率", report.get("beat_hit_rate")),
        ("闪光违规", report.get("flash_violations")),
        ("渲染帧数", report.get("frames")),
        ("渲染耗时（秒）", report.get("render_sec")),
    ]
    for label, value in rows:
        shown = "未提供" if value is None else str(value)
        lines.append(f"- **{label}**：{shown}")
    lines.append("")
    gates = bundle.gates()
    lines.append("## 门禁")
    lines.append("")
    for name, enabled in gates.items():
        lines.append(f"- {name}：{'开' if enabled else '关'}")
    lines.append("")
    lines.append("## 仍需人工确认")
    lines.append("")
    lines.append("- 类型符合度的人工判定（不看字能否认出对象 / 9 张剪影配准 ≤1px）")
    if bundle.cues is not None and bundle.cues.init_count():
        lines.append(f"- cues 中 {bundle.cues.init_count()} 项为 `init`，需用耳朵确认")
    return "\n".join(lines) + "\n"


def render_assets_gap(bundle: MvBundle) -> str:
    """素材缺口清单（来自 plan 的 material_strategy.gaps）。"""
    lines = [f"# 素材缺口 — {bundle.mv_id}", ""]
    gaps: list[str] = []
    if bundle.plan is not None and bundle.plan.material_strategy is not None:
        gaps = list(bundle.plan.material_strategy.gaps or [])
    gaps = gaps or ["未登记"]
    lines.extend(f"- {gap}" for gap in gaps)
    return "\n".join(lines) + "\n"


def render_credits(bundle: MvBundle) -> str:
    fonts = (bundle.manifest.get("fonts") or {})
    lines = [
        f"# CREDITS — {bundle.mv_id}",
        "",
        "- 本片为程序化生成（HyperFrames 逐帧渲染），除用户提供的素材外画面均由代码绘制。",
        "- 字体仅使用 OFL 授权字体。",
        "",
        "## 字体",
        "",
    ]
    for role, name in sorted(fonts.items()):
        lines.append(f"- {role}: {name}")
    if not fonts:
        lines.append("- （未登记）")
    return "\n".join(lines) + "\n"


def pack_dist(
    bundle: MvBundle,
    out_dir: str | Path,
    *,
    preview: Optional[str | Path] = None,
) -> dict[str, Any]:
    """生成交付六件套；返回 ``{path, files, missing}``。"""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    if preview is not None and Path(preview).exists():
        shutil.copyfile(preview, target / "preview.mp4")
        written.append("preview.mp4")

    (target / "storyboard.md").write_text(render_storyboard(bundle), encoding="utf-8")
    written.append("storyboard.md")

    if bundle.cues is not None:
        payload = {
            "grid": {
                "bpm": bundle.cues.grid.bpm,
                "beat_zero_sec": bundle.cues.grid.beat_zero_sec,
            },
            "items": [item.model_dump() for item in bundle.cues.items],
        }
        (target / "cues.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append("cues.json")

    if bundle.plan is not None:
        (target / "plan.md").write_text(
            render_plan_markdown(bundle.plan, bundle.brief or None), encoding="utf-8"
        )
        written.append("plan.md")
    if bundle.brief:
        (target / "brief.md").write_text(
            render_brief_markdown(bundle.brief), encoding="utf-8"
        )
        written.append("brief.md")

    (target / "report.json").write_text(
        json.dumps(bundle.report or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written.append("report.json")

    (target / "selftest.md").write_text(render_selftest(bundle), encoding="utf-8")
    written.append("selftest.md")
    (target / "assets_gap.md").write_text(render_assets_gap(bundle), encoding="utf-8")
    written.append("assets_gap.md")
    (target / "CREDITS.md").write_text(render_credits(bundle), encoding="utf-8")
    written.append("CREDITS.md")

    missing = [name for name in DIST_FILES if not (target / name).exists()]
    return {"path": str(target), "files": written, "missing": missing}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 坏 JSON 当空处理，由 validate 报
        return default


__all__ = [
    "DIST_FILES",
    "MvBundle",
    "REQUIRED_FILES",
    "load_bundle",
    "pack_dist",
    "render_assets_gap",
    "render_credits",
    "render_selftest",
    "render_storyboard",
    "validate_bundle",
]
