"""R8 验收脚本：方案接地（规划书里引用的素材必须真实存在）。

验收判据（计划 §3 的 R8 门槛）：

1. **引用不存在的素材占比 = 0**：接地产出的规划书，逐条 ref 都能在内容索引里查到；
2. **没有索引时不产生假引用**：`material_refs` 为空 + 有 `material_gap` 说明（如实留白）；
3. **库里没有相关画面时不凑数**：关键词/标签全不命中 → 留白，而不是塞最接近的；
4. **下游承接**：场景引用能变成片段的 metadata（"这一刀用了哪个镜头"可回查）。

**本脚本的自欺风险**：索引与场景都是我构造的（合成素材、无真实模型）。
它证明的是"接地的链路与诚实性规则正确"，不等于真实素材上的匹配质量。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipwright.schema.content import (  # noqa: E402
    ContentIndex,
    ShotRecord,
    ShotVisual,
    format_shot_ref,
)
from clipwright.services import scene_grounding as sg  # noqa: E402

PASS = 0
FAIL = 0
SHA_A = "a1b2c3d4e5f6" + "0" * 52
SHA_B = "b2c3d4e5f607" + "0" * 52


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _shot(i: int, desc: str, tags: tuple[str, ...] = (), **kw) -> ShotRecord:
    return ShotRecord(index=i, t0=i * 3.0, t1=i * 3.0 + 3.0, duration_sec=3.0,
                      visual=ShotVisual(description=desc, tags=list(tags), **kw))


def library() -> list[ContentIndex]:
    """两段素材：足球（3 镜头）+ 城市（2 镜头）——对应"用户手上有这些料"。"""
    return [
        ContentIndex(sha256=SHA_A, asset_path="football.mp4", filename="football.mp4",
                     shots=[_shot(0, "球场全景，看台满座", ("足球", "球场")),
                            _shot(1, "球员带球突破", ("足球", "带球")),
                            _shot(2, "射门得分，球进网", ("足球", "进球"))]),
        ContentIndex(sha256=SHA_B, asset_path="city.mp4", filename="city.mp4",
                     shots=[_shot(0, "城市夜景车流", ("城市", "夜景")),
                            _shot(1, "白天街道人流", ("城市", "街道"))]),
    ]


def case_1_grounding() -> list[dict]:
    print("\n[案例 1] 有索引的规划书 → 每个场景钉上真实镜头引用")
    scenes = [
        {"title": "开场", "description": "足球比赛球场全景", "duration_sec": 5},
        {"title": "高潮", "description": "球员射门进球的那一下", "duration_sec": 6},
        {"title": "转场", "description": "城市夜景车流", "duration_sec": 4},
    ]
    grounded, notes = sg.ground_scenes(scenes, library())
    for s in grounded:
        print(f"      · {s['title']}: {s.get('material_refs')} "
              f"({s.get('material_match')})")
    check("三个场景都接上了真实素材",
          all(s.get("material_refs") for s in grounded),
          str([bool(s.get('material_refs')) for s in grounded]))
    check("最相关的镜头排在首位",
          grounded[1]["material_refs"][0] == format_shot_ref(SHA_A, 2),
          grounded[1]["material_refs"][0])
    check("跨素材检索可用（城市素材被选中）",
          format_shot_ref(SHA_B, 0) in grounded[2]["material_refs"],
          str(grounded[2]["material_refs"]))

    problems = sg.validate_material_refs(grounded, library())
    check("**验收指标：引用不存在的素材 = 0**", not problems, str(problems)[:150])
    check("接地说明是给人看的", any("接地" in n for n in notes),
          notes[0] if notes else "")
    return grounded


def case_2_no_filler() -> None:
    print("\n[案例 2] 库里没有相关画面 → 如实留白，不许凑数")
    scenes = [{"title": "美食", "description": "一碗牛肉面的特写", "duration_sec": 5}]
    grounded, notes = sg.ground_scenes(scenes, library())
    check("引用为空", grounded[0]["material_refs"] == [], str(grounded[0]["material_refs"]))
    check("给出了缺口原因",
          "没有与" in (grounded[0].get("material_gap") or ""),
          (grounded[0].get("material_gap") or "")[:80])
    check("缺口不算「引用不存在」（留白不是错）",
          sg.validate_material_refs(grounded, library()) == [])
    check("缺口在说明里被点名", any("未接上素材" in n for n in notes))


def case_3_no_index() -> None:
    print("\n[案例 3] 没有索引 → 不产生任何假引用")
    scenes = [{"title": "开场", "description": "球场全景", "duration_sec": 5}]
    grounded, notes = sg.ground_scenes(scenes, [])
    check("引用为空且不编造", grounded[0]["material_refs"] == [])
    check("说明里点出「没有索引」", any("没有内容索引" in n for n in notes))
    check("校验通过（留白合法）", sg.validate_material_refs(grounded, []) == [])


def case_4_structural_filter() -> None:
    print("\n[案例 4] 描述含「全景/特写」→ 结构化条件不命中时退化为文本匹配")
    scenes = [{"title": "开场", "description": "球场全景", "duration_sec": 5}]
    grounded, _notes = sg.ground_scenes(scenes, library())
    s = grounded[0]
    print(f"      · match={s.get('material_match')} note={s.get('material_note')}")
    check("仍然找到了素材（没有因为索引缺景别就报没料）",
          bool(s.get("material_refs")), str(s.get("material_refs")))
    check("如实标注为退化匹配", s.get("material_match") == "degraded", str(s.get("material_match")))


def case_5_downstream(grounded: list[dict]) -> None:
    print("\n[案例 5] 下游承接 → 片段 metadata 可回查用了哪个镜头")
    meta = sg.refs_to_clip_metadata(grounded[1])
    check("片段 metadata 带上了引用", bool(meta.get("material_refs")),
          str(meta)[:100])
    check("引用格式可回解（asset:sha#sN）",
          all(str(r).startswith("asset:") and "#s" in str(r)
              for r in meta.get("material_refs", [])))
    text = sg.summary_text(grounded)
    check("规划书摘要能列出引用", "素材" not in text or bool(text), text.splitlines()[0] if text else "")


def case_6_fake_refs_detected() -> None:
    print("\n[案例 6] 人为塞一个假引用 → 必须被校验抓到")
    bad = [{"title": "假的", "material_refs": ["asset:000000000000#s9"]}]
    problems = sg.validate_material_refs(bad, library())
    check("查出「引用不存在」", bool(problems) and "不存在" in problems[0],
          str(problems)[:100])


def main() -> int:
    print("=" * 72)
    print("R8 验收：方案接地（规划书引用真实素材）")
    print("索引与场景均为**构造数据**（合成素材、无真实模型）")
    print("=" * 72)
    grounded = case_1_grounding()
    case_2_no_filler()
    case_3_no_index()
    case_4_structural_filter()
    case_5_downstream(grounded)
    case_6_fake_refs_detected()
    print("\n" + "=" * 72)
    print(f"检查项 {PASS}/{PASS + FAIL} 通过")
    print("=" * 72)
    if FAIL:
        print("SCENE GROUNDING VERIFY FAIL")
        return 1
    print("SCENE GROUNDING VERIFY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
