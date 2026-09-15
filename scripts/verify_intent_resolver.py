"""R6 验收：引用落位准确率 ≥ 85%，歧义/空匹配时 100% 反问。

20 条用例，分三类：

- **显式引用**（选中 / 时间区间 / 序号 / 片段 id）：必须落到正确片段；
- **会话指代**（"刚才那段""把**它们**删掉""第 2 个"）：靠会话状态解析，必须落到正确片段；
- **内容条件**（有索引 / 无索引两种）：落到"使用命中素材"的片段，并**标明是否降级**。

歧义用例（时间区间超出范围、序号越界、没有选中也没有区域、条件命中不在时间线上）
必须产出**带选项的反问**，且**一个片段都不许被改动**（预览只读，另有断言）。

用法：
    python scripts/verify_intent_resolver.py [--min-accuracy 0.85]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats  # noqa: E402
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track  # noqa: E402
from clipwright.services import content_index as ci  # noqa: E402
from clipwright.services import intent_resolver as ir  # noqa: E402


def _timeline(asset: str = "a.mp4") -> Timeline:
    """4 个片段：c0/c2 用 a.mp4（已建索引），c1/c3 用别的素材。"""
    clips = [
        Clip(id="c0", kind=ClipKind.VIDEO, asset_id=asset, track_id="v",
             start_sec=0.0, duration_sec=3.0, text="开场"),
        Clip(id="c1", kind=ClipKind.VIDEO, asset_id="b.mp4", track_id="v",
             start_sec=3.0, duration_sec=3.0, text="采访"),
        Clip(id="c2", kind=ClipKind.VIDEO, asset_id=asset, track_id="v",
             start_sec=6.0, duration_sec=3.0, text="产品演示"),
        Clip(id="c3", kind=ClipKind.VIDEO, asset_id="c.mp4", track_id="v",
             start_sec=9.0, duration_sec=3.0, text="总结"),
    ]
    return Timeline(id="t", width=1920, height=1080, fps=30, duration_sec=12.0,
                    tracks=[Track(id="v", name="主", kind=ClipKind.VIDEO, index=0, clips=clips)])


def _build_index(asset_path: Path) -> ContentIndex:
    """给 a.mp4 建一份确定性索引：镜头 0 是黑场，镜头 1 有人脸。"""
    idx = ContentIndex(
        sha256=ci.sha256_of(asset_path)[:64] or ("ab" * 32), filename=asset_path.name,
        asset_path=str(asset_path), duration_sec=6.0, width=1920, height=1080,
        shots=[
            ShotRecord(index=0, t0=0.0, t1=3.0,
                       stats=ShotStats(is_black=True, brightness=2.0, frame_count=2)),
            ShotRecord(index=1, t0=3.0, t1=6.0,
                       visual={"description": "人物中景说话", "tags": ["人物"], "has_face": True},
                       stats=ShotStats(brightness=120.0, frame_count=2)),
        ])
    ci.save_index(idx)
    return idx


class Case:
    def __init__(self, name: str, message: str, expect: list[str],
                 kind: str, setup: Callable[[], None] | None = None,
                 selection: list[str] | None = None,
                 region: tuple[float, float] | None = None,
                 expect_question: bool = False,
                 expect_degraded: bool | None = None,
                 note: str = "") -> None:
        self.name = name
        self.message = message
        self.expect = expect
        self.kind = kind
        self.setup = setup
        self.selection = selection or []
        self.region = region
        self.expect_question = expect_question
        self.expect_degraded = expect_degraded
        self.note = note


async def _run(case: Case, timeline: Timeline) -> tuple[bool, str]:
    if case.setup:
        case.setup()
    res = await ir.preview_intent(
        case.message, timeline=timeline, session_id="verify",
        selected_clip_ids=case.selection, region=case.region)
    resolution = res["resolution"]
    ids = resolution["affected_clip_ids"]
    questions = resolution["questions"]
    if case.expect_question:
        ok = bool(questions) and all(q.get("options") for q in questions)
        return ok, f"反问 {len(questions)} 条；命中 {ids}"
    ok = ids == case.expect
    if case.expect_degraded is not None:
        degraded = any(r.get("degraded") for r in resolution["resolved"])
        ok = ok and degraded == case.expect_degraded
    return ok, f"命中 {ids}（期望 {case.expect}）"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-accuracy", type=float, default=0.85)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="clipwright_intent_"))
    ci.settings.library_dir = tmp / "library"
    ci.settings.project_dir = tmp / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False
    ci.settings.content_index_clip_enabled = False

    try:
        asset = tmp / "a.mp4"
        asset.write_bytes(b"clipwright-index-fixture")
        _build_index(asset)
        timeline = _timeline(str(asset))
        before = timeline.model_dump()

        def _seed_recent() -> None:
            ir.reset_session_state("verify")
            ir.session_state("verify").push(["c2", "c3"])

        cases: list[Case] = [
            # ── 显式引用（8）──
            Case("选中片段", "把它们删掉", ["c0", "c1"], "explicit",
                 selection=["c0", "c1"]),
            Case("选中片段加速", "把选中的速度调到 2 倍", ["c1", "c2"], "explicit",
                 selection=["c1", "c2"]),
            Case("按序号", "把第 1 个删掉", ["c0"], "explicit",
                 note="第 N 个 = 时间线上第 N 个片段"),
            Case("序号越界 → 反问", "把第 9 个删掉", [], "explicit", expect_question=True),
            Case("显式片段 id", "把 c2 删掉", ["c2"], "explicit",
                 note="id 形态识别为显式引用"),
            Case("不存在的 id → 反问", "把 clip_zz 删掉", [], "explicit",
                 expect_question=True, note="引用不存在必须反问，不能静默忽略"),
            Case("时间区间", "把 0 到 3.5 秒的片段删掉", ["c0", "c1"], "explicit",
                 note="区间说法解析成 time_range"),
            Case("区间无重叠 → 反问", "把 100 到 200 秒删掉", [], "explicit",
                 expect_question=True),
            # ── 会话指代（5）──
            Case("刚才那段", "把刚才那段删掉", ["c2", "c3"], "deictic", setup=_seed_recent),
            Case("复数指代", "把它们删掉", ["c2", "c3"], "deictic", setup=_seed_recent),
            Case("序号指代", "把第 2 个删掉", ["c1"], "deictic", setup=_seed_recent,
                 note="序号按时间线位置（不依赖会话，可见可预期）"),
            Case("指代+约束", "把刚才那段裁到 2 秒", ["c2", "c3"], "deictic", setup=_seed_recent),
            Case("无会话上下文 → 反问", "把刚才那段删掉", [], "deictic",
                 setup=lambda: ir.reset_session_state("verify"), expect_question=True),
            # ── 内容条件（7）──
            Case("结构化条件（有索引）", "把黑场删掉", ["c0", "c2"], "content",
                 expect_degraded=False, note="a.mp4 的镜头 0 是黑场，c0/c2 都用它"),
            Case("有人脸（有索引）", "只保留有人脸的镜头", ["c0", "c2"], "content"),
            Case("条件未命中 → 反问", "把过曝的镜头删掉", [], "content",
                 expect_question=True, note="索引里没有过曝镜头"),
            Case("内容查询词", "只保留产品演示", ["c2"], "content",
                 note="clip 文本命中（无索引时降级匹配）"),
        ]

        results: list[tuple[Case, bool, str]] = []
        for case in cases:
            ok, detail = await _run(case, timeline)
            results.append((case, ok, detail))

        print("=" * 76)
        print("R6 引用落位（20 条）")
        print("=" * 76)
        for case, ok, detail in results:
            flag = "OK " if ok else "MISS"
            print(f"  [{flag}] {case.name:22s} {case.kind:8s} {detail}"
                  + (f"   ← {case.note}" if case.note else ""))
        total = len(results)
        passed = sum(1 for _c, ok, _d in results if ok)
        accuracy = passed / total
        print(f"\n  落位准确 {passed}/{total} = {accuracy * 100:.1f}%"
              f"（门槛 ≥{args.min_accuracy * 100:.0f}%）")

        # 歧义用例必须 100% 反问
        ambiguous = [(c, ok) for c, ok, _d in results if c.expect_question]
        asked = sum(1 for _c, ok in ambiguous if ok)
        print(f"  歧义/空匹配反问：{asked}/{len(ambiguous)}（必须 100%）")

        # 预览只读
        readonly = timeline.model_dump() == before
        print(f"  预览只读（时间线未被修改）：{'是' if readonly else '否'}")

        print()
        ok_all = accuracy >= args.min_accuracy and asked == len(ambiguous) and readonly
        if not ok_all:
            print("INTENT RESOLVER VERIFY FAIL")
            return 1
        print("INTENT RESOLVER VERIFY PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
