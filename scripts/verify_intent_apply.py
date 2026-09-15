"""R7 验收脚本：3 条"一句话 → 成片"跑通（plan §4 的 M2 门槛）。

验收判据（不是"脚本没报错"，而是**结果对不对**）：

1. 每条指令必须真的改变时间线，且改完的**片段数/总时长/位置**与人工推演的期望一致；
2. 带歧义的指令必须**停在反问**而不是猜（`./verify_intent_resolver.py` 已单独验过这一层）；
3. dry-run 必须**只读**：预览前后时间线逐字节一致；
4. 执行前后 `Timeline` 仍是合法模型（Pydantic 校验通过）。

**本脚本的自欺风险**：三条指令与期望值都是我写的，素材是合成时间线（无真实素材、
无真实模型）。它证明的是"编译与执行链路正确"，**不等于**真实素材上剪得好不好。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipwright.schema.timeline import Clip, Timeline, Track  # noqa: E402
from clipwright.services.intent_apply import plan_validate_apply  # noqa: E402
from clipwright.services.intent_resolver import reset_session_state  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def synthetic_timeline() -> Timeline:
    """5 个各 4 秒的镜头（0/4/8/12/16s，总长 20s）——"一段素材剪出集锦"的最小模型。"""
    clips = [
        Clip(id=f"c{i}", kind="video", asset_id=f"shot{i}.mp4", track_id="tr_v",
             start_sec=i * 4.0, duration_sec=4.0)
        for i in range(5)
    ]
    clips.append(Clip(id="cap0", kind="caption", asset_id="", track_id="tr_cap",
                      start_sec=0.0, duration_sec=4.0, text="开场"))
    return Timeline(
        id="tl_demo", fps=30, width=1920, height=1080,
        tracks=[
            Track(id="tr_v", name="V1", kind="video", index=0, clips=clips[:5]),
            Track(id="tr_cap", name="字幕", kind="caption", index=1, clips=[clips[5]]),
        ],
    )


def positions(tl: Timeline, track_id: str) -> list[tuple[str, float, float]]:
    for track in tl.tracks:
        if track.id == track_id:
            return [(c.id, round(float(c.start_sec), 3), round(float(c.duration_sec), 3))
                    for c in track.clips]
    return []


async def case_1() -> None:
    print("\n[案例 1] 「删掉第2个片段」——按时间线位置删除并收拢")
    reset_session_state("r7_1")
    tl = synthetic_timeline()
    before = tl.model_dump()

    preview = await plan_validate_apply("删掉第2个片段", timeline=tl, session_id="r7_1",
                                        project_id=None)
    check("dry-run 不改时间线", tl.model_dump() == before)
    check("dry-run 给出改动预览", bool(preview["report"]["preview"]),
          preview["report"]["preview"][0] if preview["report"]["preview"] else "")
    check("dry-run 之后仍可执行（未被 block）", not preview["blocked"])

    result = await plan_validate_apply("删掉第2个片段", timeline=tl, session_id="r7_1",
                                       project_id=None, apply=True)
    check("实际执行成功", result["applied"] is True, str(result.get("problems"))[:120])
    if not result["applied"]:
        return
    new = Timeline.model_validate(result["timeline"])
    got = positions(new, "tr_v")
    # 删 c1（4-8s）→ 后面三个各左移 4s；字幕轨没被删，一像素不动
    check("视频轨片段数 5 → 4", len(got) == 4, str([g[0] for g in got]))
    check("后续片段左移补齐", got == [("c0", 0.0, 4.0), ("c2", 4.0, 4.0),
                                    ("c3", 8.0, 4.0), ("c4", 12.0, 4.0)], str(got))
    check("总时长 20s → 16s", round(float(new.duration_sec), 3) == 16.0,
          f"{new.duration_sec}")
    check("字幕轨未被波及", positions(new, "tr_cap") == [("cap0", 0.0, 4.0)],
          str(positions(new, "tr_cap")))


async def case_2() -> None:
    print("\n[案例 2] 「把所有片段速度调成1.5倍」——整体字段修改")
    reset_session_state("r7_2")
    tl = synthetic_timeline()
    result = await plan_validate_apply("把所有片段速度调成1.5倍", timeline=tl, session_id="r7_2",
                                       project_id=None, apply=True)
    check("实际执行成功", result["applied"] is True, str(result.get("problems"))[:120])
    if not result["applied"]:
        return
    new = Timeline.model_validate(result["timeline"])
    speeds = {c.id: round(float(c.speed), 3) for t in new.tracks for c in t.clips}
    check("5 个视频片段速度均为 1.5", all(v == 1.5 for k, v in speeds.items() if k.startswith("c")),
          str(speeds))
    check("总时长不变（变速不改时长）", round(float(new.duration_sec), 3) == 20.0,
          f"{new.duration_sec}")


async def case_3() -> None:
    print("\n[案例 3] 「剪到10秒」——总时长约束（尾部裁剪）")
    reset_session_state("r7_3")
    tl = synthetic_timeline()
    result = await plan_validate_apply("剪到10秒", timeline=tl, session_id="r7_3",
                                       project_id=None, apply=True)
    check("实际执行成功", result["applied"] is True, str(result.get("problems"))[:120])
    if not result["applied"]:
        return
    new = Timeline.model_validate(result["timeline"])
    got = positions(new, "tr_v")
    check("总时长精确到 10.0s", abs(float(new.duration_sec) - 10.0) < 1e-6,
          f"{new.duration_sec}")
    check("尾部裁掉 2 个片段、最后一段裁短",
          got == [("c0", 0.0, 4.0), ("c1", 4.0, 4.0), ("c2", 8.0, 2.0)], str(got))


async def case_4_questions() -> None:
    print("\n[案例 4] 「裁到3秒」无选中——必须反问，不许猜")
    reset_session_state("r7_4")
    tl = synthetic_timeline()
    before = tl.model_dump()
    result = await plan_validate_apply("裁到3秒", timeline=tl, session_id="r7_4",
                                       project_id=None, apply=True)
    check("未执行（blocked）", result["applied"] is False and result["blocked"] is True)
    check("时间线未被动过", tl.model_dump() == before)
    check("给出可用于反问的问题", len(result["resolution"].get("questions") or []) > 0,
          str((result["resolution"].get("questions") or [{}])[0].get("text", ""))[:80])

    answered = await plan_validate_apply("裁到3秒", timeline=tl, session_id="r7_4",
                                         project_id=None, answers={"no_match": "c0"}, apply=True)
    check("回答后可以执行", answered["applied"] is True, str(answered.get("problems"))[:120])
    if answered["applied"]:
        new = Timeline.model_validate(answered["timeline"])
        check("被选中片段裁到 3s 且后续前移",
              positions(new, "tr_v")[0] == ("c0", 0.0, 3.0), str(positions(new, "tr_v")[:2]))


async def case_5_unparsed() -> None:
    print("\n[案例 5] 完全解析不出操作——必须如实回答，不许静默成功")
    reset_session_state("r7_5")
    tl = synthetic_timeline()
    before = tl.model_dump()
    result = await plan_validate_apply("把这个视频变得更有感染力一点", timeline=tl,
                                       session_id="r7_5", project_id=None, apply=True)
    check("未执行", result["applied"] is False)
    check("blocked 且说明了原因", result["blocked"] is True and bool(result.get("problems")),
          str((result.get("problems") or [{}])[-1].get("message", ""))[:100])
    check("时间线未被动过", tl.model_dump() == before)


async def case_6_hallucination() -> None:
    print("\n[案例 6] 幻觉操作——必须在编译阶段被拦住")
    reset_session_state("r7_6")
    tl = synthetic_timeline()
    before = tl.model_dump()
    result = await plan_validate_apply("把这段的 assets 字段改成 secret.mp4", timeline=tl,
                                       session_id="r7_6", project_id=None, apply=True)
    check("未执行", result["applied"] is False)
    check("补丁为空", result["patch"]["ops"] == [], str(result["patch"]["ops"])[:100])
    check("时间线未被动过", tl.model_dump() == before)


async def main() -> int:
    print("=" * 72)
    print("R7 验收：一句话 → 成片（plan / dry-run / apply）")
    print("素材为**合成时间线**（无真实素材与模型），验证的是链路正确性")
    print("=" * 72)
    await case_1()
    await case_2()
    await case_3()
    await case_4_questions()
    await case_5_unparsed()
    await case_6_hallucination()
    print("\n" + "=" * 72)
    total = PASS + FAIL
    print(f"检查项 {PASS}/{total} 通过")
    print("=" * 72)
    if FAIL:
        print("INTENT APPLY VERIFY FAIL")
        return 1
    print("INTENT APPLY VERIFY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
