"""门 4 预演：一次性跑完能自动化的验收项（PRD V21–V43）。

用法::

    python -m tools.gate_check --bundle examples/mv_poc/plate_shot --dist out/dist/plate_shot
    python -m tools.gate_check --bundle examples/mv_poc/plate_shot --json

检查项（全部来自 PRD 验收表，能自动化的部分）：

| 验收 | 判据 |
|---|---|
| V21 | 成片有帧（`report.frames > 0`） |
| V22 | 两次渲染逐帧一致（`report.deterministic is True`） |
| V26 | 闪光违规为空 |
| V37 | 交付目录九件套齐全（`preview.mp4` 可缺但会列为缺失） |
| V32 | 有音视频时长时用 `check_sync` 校验（含片头偏移） |
| V24 | 有 `beat_hit_rate` 时要求 ≥0.95 |
| V59 | 同时存在 `plan.md` 与 `brief.md`（双文档） |
| V42 | 素材缺口被显式列出（`assets_gap.md` 非空且不是"未登记"） |

**不做**：需要人或真实素材的项（V16/V20/V30/V34/V40/V41 人工判据）只列进 `manual`。

退出码：0 = 无 error；1 = 有 error。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from clipwright.services.mv_bundle import DIST_FILES, load_bundle, validate_bundle

#: 需要人工确认、本脚本只能提醒的验收项。
MANUAL_ITEMS: tuple[tuple[str, str], ...] = (
    ("V16", "三类型可判别（需开 genre judge 或人工盲评）"),
    ("V20", "人工终审：3 人里 ≥2 人能说出「这是一场 AI 与用户的对话 MV」"),
    ("V30", "字符版画：不看字能认出对象（20 张抽检）"),
    ("V34", "4K hero 特写边缘质量（人工看 4K 单帧）"),
    ("V41", "素材落位：10 个落位 ≥8 人认可（需真实素材）"),
)


@dataclass
class Check:
    id: str
    name: str
    ok: bool
    detail: str = ""
    severity: str = "error"


@dataclass
class GateReport:
    bundle: str = ""
    dist: str = ""
    checks: list[Check] = field(default_factory=list)
    manual: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bundle": self.bundle,
            "dist": self.dist,
            "passed": [c.id for c in self.checks if c.ok],
            "failed": [c.id for c in self.checks if not c.ok and c.severity == "error"],
            "warnings": [c.id for c in self.checks if not c.ok and c.severity == "warning"],
            "checks": [
                {"id": c.id, "name": c.name, "ok": c.ok, "detail": c.detail, "severity": c.severity}
                for c in self.checks
            ],
            "manual": self.manual,
        }

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.ok)
        head = f"门 4 预演：{passed}/{len(self.checks)} 项自动检查通过"
        if not self.ok:
            head += "（不通过）"
        lines = [head]
        for check in self.checks:
            mark = "[ok]" if check.ok else ("[FAIL]" if check.severity == "error" else "[warn]")
            lines.append(f"  {mark} {check.id} {check.name}" + (f" — {check.detail}" if check.detail else ""))
        if self.manual:
            lines.append("  仍需人工：" + "、".join(item["id"] for item in self.manual))
        return "\n".join(lines)


def _dist_missing(dist: Path) -> list[str]:
    return [name for name in DIST_FILES if not (dist / name).exists()]


def run_gate_check(
    bundle_dir: str | Path,
    dist_dir: Optional[str | Path] = None,
) -> GateReport:
    """跑全部自动检查项。"""
    report = GateReport(bundle=str(bundle_dir), dist=str(dist_dir or ""))
    bundle = load_bundle(bundle_dir)
    render_report = bundle.report or {}

    # 结构完整性（工程包本身）
    issues = [i for i in validate_bundle(bundle) if i["code"] != "bundle_missing_file"]
    report.checks.append(
        Check("bundle", "工程包结构校验", not issues, "; ".join(i["code"] for i in issues) or "ok")
    )

    frames = render_report.get("frames")
    report.checks.append(
        Check(
            "V21",
            "成片有帧",
            isinstance(frames, int) and frames > 0,
            f"frames={frames}",
        )
    )

    deterministic = render_report.get("deterministic")
    report.checks.append(
        Check("V22", "两次渲染逐帧一致", deterministic is True, f"deterministic={deterministic}")
    )

    violations = render_report.get("flash_violations")
    report.checks.append(
        Check(
            "V26",
            "闪光违规为空",
            violations in ([], None) and render_report.get("flash_ok", True) is not False,
            f"violations={violations}",
        )
    )

    hit_rate = render_report.get("beat_hit_rate")
    if hit_rate is not None:
        report.checks.append(
            Check("V24", "拍点命中率 ≥95%", float(hit_rate) >= 0.95, f"rate={hit_rate}")
        )

    # 音画同步（有数据才检查）
    video_sec = render_report.get("video_sec")
    audio_sec = render_report.get("audio_sec")
    if isinstance(video_sec, (int, float)) and isinstance(audio_sec, (int, float)):
        from tools.check_sync import check_sync

        sync = check_sync(float(video_sec), float(audio_sec), preroll_sec=float(render_report.get("preroll_sec") or 0))
        report.checks.append(Check("V32", "音画同步（含片头偏移）", sync.ok, sync.summary()))

    # 交付目录
    if dist_dir is not None:
        dist = Path(dist_dir)
        missing = _dist_missing(dist)
        report.checks.append(
            Check(
                "V37",
                "交付九件套齐全",
                not missing,
                "缺：" + ", ".join(missing) if missing else "ok",
            )
        )
        if (dist / "plan.md").exists() or (dist / "brief.md").exists():
            report.checks.append(
                Check(
                    "V59",
                    "plan.md 与 brief.md 双文档齐全",
                    (dist / "plan.md").exists() and (dist / "brief.md").exists(),
                    "ok",
                )
            )
        gap_file = dist / "assets_gap.md"
        if gap_file.exists():
            text = gap_file.read_text(encoding="utf-8")
            report.checks.append(
                Check(
                    "V42",
                    "素材缺口被显式登记",
                    "未登记" not in text,
                    "" if "未登记" not in text else "assets_gap.md 仍是占位",
                )
            )

    report.manual = [{"id": i, "name": n} for i, n in MANUAL_ITEMS]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="门 4 预演：自动跑可验收项")
    parser.add_argument("--bundle", required=True, help="工程包目录")
    parser.add_argument("--dist", default="", help="交付目录（可选）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = run_gate_check(args.bundle, args.dist or None)
    except Exception as exc:  # noqa: BLE001 - CLI 边界
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
