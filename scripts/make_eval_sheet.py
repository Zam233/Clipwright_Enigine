"""生成"一句话 → 成片"的人工评分表（R10 未完成的主观评分项）。

R10 的验收指标里有一条我**做不了**的："3 个项目，主观 ≥ 4/5 且改动可解释"——
主观评分必须有真实的人来打，模型自己给自己打分不算数。

所以这里交付的是**评分工具**而不是评分结果：脚本跑一遍真实素材的端到端链路，
把每一步（解析出的意图 / dry-run 预览 / 执行结果）落成一张表，
你（或任何评审者）对着表打 1–5 分并写明理由，再用 `--score` 汇总。

    # 1) 生成评分表（跑真实链路，产出 markdown + json）
    python scripts/make_eval_sheet.py --dir data/pexels_real --out eval/eval_sheet.md

    # 2) 人工在 markdown 里给每项打分并写理由（表格里 0 的位置）

    # 3) 汇总
    python scripts/make_eval_sheet.py --score eval/eval_sheet.json

设计上刻意让"人话"占主导：每一行都带**模型实际说了什么/实际改了什么**，
评审者不需要看代码也能判断"这是不是我要的结果"。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 评分项：(代号, 说明, 这一项在问什么)
CRITERIA = (
    ("C1", "意图理解", "这句话被理解成了你要的意思吗？"),
    ("C2", "改动正确", "时间线改成你想要的样子了吗？"),
    ("C3", "改动可解释", "从「意图 → 预览 → 结果」这条链能看懂它为什么这么改吗？"),
    ("C4", "不过度改动", "有没有动到你没让它动的东西？"),
    ("C5", "失败时诚实", "它做不到的时候，有没有如实说清楚（而不是假装成功）？"),
)

TASKS = (
    "删掉第2个片段",
    "把所有片段速度调成1.5倍",
    "剪到10秒",
    "把画面调亮一点",
    "做成一个有感染力的集锦",
)


async def build_sheet(material_dir: Path) -> dict:
    from clipwright.services import content_index as ci
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services.intent_apply import plan_validate_apply

    entries = json.loads((material_dir / "manifest.json").read_text(encoding="utf-8"))
    materials = [material_dir / e["file"] for e in entries
                 if (material_dir / e["file"]).exists()]
    workdir = Path(tempfile.mkdtemp(prefix="clipwright_eval_"))
    ci.settings.library_dir = workdir / "library"
    ci.settings.project_dir = workdir / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False

    indexes = [await ci.build_index(m, force=True, mirror=False) for m in materials]
    clips, cursor = [], 0.0
    for idx in indexes:
        for i, shot in enumerate(idx.shots):
            dur = round(max(0.2, float(shot.duration_sec)), 3)
            clips.append(Clip(id=f"c{len(clips):03d}", kind="video",
                              asset_id=str(idx.asset_path), track_id="tr_v",
                              start_sec=round(cursor, 3), duration_sec=dur,
                              source_offset_sec=round(float(shot.t0), 3)))
            cursor += dur
    timeline = Timeline(id="tl_eval", fps=float(indexes[0].fps or 25.0),
                        width=int(indexes[0].width or 640),
                        height=int(indexes[0].height or 360),
                        tracks=[Track(id="tr_v", name="V1", kind="video", index=0,
                                      clips=clips)])

    items = []
    # 传一个"用户已选中前两个片段"的上下文：否则 `set` 类指令（"把画面调亮一点"）
    # 会因为"没有选中片段"而反问——那是**正确但会让评审误判为失败**的行为。
    # 评分表要评的是"有选中时它做得对不对"，所以这里显式给出选中。
    selection = [c.id for c in clips[:2]]
    for message in TASKS:
        preview = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                           selected_clip_ids=selection)
        result = await plan_validate_apply(message, timeline=timeline, session_id="eval",
                                          selected_clip_ids=selection, apply=True)
        items.append({
            "task": message,
            "intent": preview.get("intent") or "（未解析出）",
            "dry_run": (preview.get("report") or {}).get("preview") or [],
            "problems": [p.get("message") for p in (preview.get("problems") or [])],
            "applied": bool(result.get("applied")),
            "blocked": bool(result.get("blocked")),
            "before": (result.get("report") or {}).get("duration_before"),
            "after": (result.get("report") or {}).get("duration_after"),
            "clips_before": (result.get("report") or {}).get("clips_before"),
            "clips_after": (result.get("report") or {}).get("clips_clips_after")
            or (result.get("report") or {}).get("clips_after"),
            "scores": {code: 0 for code, _n, _q in CRITERIA},
            "notes": "",
        })
        if result.get("applied"):
            timeline = Timeline.model_validate(result["timeline"])
    return {"materials": [m.name for m in materials], "items": items}


def render_markdown(payload: dict) -> str:
    lines = [
        "# 「一句话 → 成片」人工评分表",
        "",
        f"素材：{len(payload['materials'])} 段（{'、'.join(payload['materials'][:4])}…）",
        "",
        "**怎么用**：逐行给 C1–C5 打 1–5 分（0 = 未评），在「理由」里写一句为什么。",
        "打完之后用 `--score` 汇总：`python scripts/make_eval_sheet.py --score eval/eval_sheet.json`",
        "",
        "（表中任务在「用户已选中前两个片段」的上下文里执行；未选中时 `set` 类指令会先反问，",
        "那是正确行为，不该算失败。）",
        "",
        "评分项：" + "；".join(f"**{c}** {n}——{q}" for c, n, q in CRITERIA),
        "",
    ]
    for i, item in enumerate(payload["items"], 1):
        lines += [
            f"## 任务 {i}：`{item['task']}`",
            "",
            f"- **解析出的意图**：{item['intent'].splitlines()[0] if item['intent'] else '—'}",
            f"- **dry-run 预览**：{'；'.join(item['dry_run'][:4]) or '—'}",
            f"- **未执行原因**：{'；'.join(item['problems']) or '（无）'}",
            f"- **实际结果**：{'已执行' if item['applied'] else '未执行'}"
            + (f"，总时长 {item['before']}s → {item['after']}s，"
               f"片段 {item['clips_before']} → {item['clips_after']} 个"
               if item["applied"] else ""),
            "",
            "| " + " | ".join(c for c, _n, _q in CRITERIA) + " | 理由 |",
            "|" + "---|" * (len(CRITERIA) + 1),
            "| " + " | ".join("0" for _ in CRITERIA) + " |  |",
            "",
        ]
    return "\n".join(lines)


def score(payload: dict) -> int:
    totals = {c: [] for c, _n, _q in CRITERIA}
    for item in payload["items"]:
        for code, value in (item.get("scores") or {}).items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if v > 0:
                totals.setdefault(code, []).append(v)
    print("=" * 60)
    print("主观评分汇总（1–5 分；0/未填不计入）")
    print("=" * 60)
    overall: list[float] = []
    for code, name, question in CRITERIA:
        values = totals.get(code) or []
        if not values:
            print(f"  {code} {name:6s}：未评分（{question}）")
            continue
        avg = sum(values) / len(values)
        overall.extend(values)
        print(f"  {code} {name:6s}：{avg:.2f} / 5（{len(values)} 项）")
    if not overall:
        print("\n还没有任何评分。请在评分表里把 0 改成 1–5。")
        return 1
    avg_all = sum(overall) / len(overall)
    print(f"\n  总体：{avg_all:.2f} / 5（{len(overall)} 个评分）")
    print("  验收门槛是「≥ 4/5」——" + ("**达标**" if avg_all >= 4 else "**未达标**"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/pexels_real")
    ap.add_argument("--out", default="eval/eval_sheet.md")
    ap.add_argument("--score", default="", help="给已有的评分结果 json 做汇总")
    args = ap.parse_args()

    if args.score:
        payload = json.loads(Path(args.score).read_text(encoding="utf-8"))
        return score(payload)

    material_dir = Path(args.dir).resolve()
    if not (material_dir / "manifest.json").exists():
        print(f"找不到 manifest：{material_dir}（先跑 fetch_pexels_testset.py）")
        return 2
    payload = asyncio.run(build_sheet(material_dir))
    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    out_json = out_md.with_suffix(".json")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"评分表已生成：{out_md}")
    print(f"评分数据（填完分数后用 --score 汇总）：{out_json}")
    print("提示：这是**工具**，不是评分结果——主观评分必须由人来打。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
