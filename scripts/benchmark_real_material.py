"""真实素材基准（Pexels）：镜头切分 / 中文检索 / 管线贯通。

R1–R10 的验收全在合成素材上，真实素材上的表现一直标注为"未验证"。
本脚本用**真实拍摄片段**补这块，三项各自有明确的真值：

1. **镜头切分**（真实像素、已知真值）：把 N 段真实素材**拼成一条多镜头视频**，
   拼接处就是硬切。用真值切点算 precision / recall / F1。
   为什么这么测：从 Pexels 随机取到的片段多半是"一个连续镜头"（实测 7 段里 7 段都是），
   拿它测切分等于没测；拼接既能保证真值精确，又用的是**真实像素**（不是纯色块）。
2. **中文检索**（真实素材、人工标注查询）：每条素材配若干中文查询（说法与素材标签**不同**），
   测 top-1 / top-3 命中率，并与随机基线对比。
3. **管线贯通**：内容问答 / 方案接地 / 一句话剪辑各跑一次，**如实报告缺口**。

用法：

    $env:CLIPWRIGHT_RAG_EMBED_PROVIDER = "sentence_transformer"
    $env:CLIPWRIGHT_RAG_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
    python scripts/benchmark_real_material.py --dir data/pexels_testset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Pexels 检索词 → 会落到该素材上的中文查询（人工标注；刻意不照抄标签）
QUERY_MAP: dict[str, list[str]] = {
    "soccer": ["球场的画面", "球员踢球的镜头", "足球比赛"],
    "match": ["球场的画面", "球员踢球的镜头"],
    "ocean": ["海边的镜头", "海浪的画面", "大海"],
    "waves": ["海浪的画面", "海边的镜头"],
    "beach": ["沙滩的画面", "海边的镜头"],
    "city": ["城市的街道", "夜里城市的街道"],
    "night": ["夜景画面", "夜里城市的街道"],
    "cooking": ["做饭的镜头", "厨房里炒菜"],
    "kitchen": ["厨房里炒菜", "做饭的镜头"],
    "food": ["食物特写", "做饭的镜头"],
    "forest": ["森林的画面", "树林里徒步"],
    "hiking": ["树林里徒步", "森林的画面"],
    "dog": ["小狗在玩", "宠物的镜头"],
    "traffic": ["街道上的车流", "城市交通"],
}

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


def queries_for(manifest_entry: dict) -> list[str]:
    """按检索词给出中文查询（人工映射；没映射到的素材不参与检索评测）。"""
    q = str(manifest_entry.get("query") or "").lower()
    out: list[str] = []
    for key, values in QUERY_MAP.items():
        if key in q:
            for v in values:
                if v not in out:
                    out.append(v)
    return out


async def build_indexes(materials: list[Path]):
    from clipwright.services import content_index as ci

    indexes = []
    for m in materials:
        started = time.perf_counter()
        idx = await ci.build_index(m, force=True, mirror=False)
        indexes.append((idx, time.perf_counter() - started))
    return indexes


def concat_real(materials: list[Path], out: Path, size: str = "640x360",
                durations: list[float] | None = None) -> list[float]:
    """把 N 段真实素材拼成一条视频，返回**真值切点时刻**（拼接处）。

    `durations` 由调用方（异步上下文里已探过）传入——这里不自己 `asyncio.run`，
    否则会撞上"事件循环已在运行"。

    分两步（先逐段归一化再 concat 解复用器）：一步到位的 `filter_complex concat`
    在这批素材上会因**帧率不同（24/25/29.97）**报 `-22 Invalid argument`；
    逐段归一化后再拼既稳，也让每段时长可精确控制（真值切点才靠得住）。
    """
    durations = list(durations or [])
    norm_dir = out.parent / "norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    w, h = size.split("x")
    for i, (m, dur) in enumerate(zip(materials, durations)):
        part = norm_dir / f"p{i}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(m),
               "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,format=yuv420p",
               "-an", "-t", f"{max(0.1, dur):.3f}",
               "-c:v", "libx264", "-preset", "veryfast", str(part)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"归一化失败 {m.name}: {r.stderr[-300:]}")
        parts.append(part)

    listfile = norm_dir / "list.txt"
    listfile.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts),
                        encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(listfile), "-c", "copy", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"拼接失败: {r.stderr[-300:]}")

    cuts: list[float] = []
    acc = 0.0
    for d in durations[:-1]:
        acc += d
        cuts.append(round(acc, 3))
    return cuts


async def case_shot_detection(materials: list[Path], workdir: Path) -> None:
    print("\n[案例 1] 真实像素上的镜头切分（拼接处 = 真值硬切）")
    from clipwright.services import content_index as ci

    picked = materials[:4] if len(materials) >= 4 else materials
    if len(picked) < 2:
        print("  素材不足 2 段，跳过")
        return
    out = workdir / "real_multishot.mp4"
    durations = []
    for m in picked:
        meta = await ci.probe_media(m)
        durations.append(float(meta.get("duration") or 0.0))
    truth = concat_real(picked, out, durations=durations)
    meta = await ci.probe_media(out)
    print(f"  拼接 {len(picked)} 段真实素材 → {out.name}"
          f"（{meta.get('duration'):.2f}s，真值切点 {truth}）")

    for thr in (0.3, 0.2, 0.12):
        cuts = await ci.detect_cut_times(out, thr)
        tp = sum(1 for t in truth if any(abs(c - t) <= 0.3 for c in cuts))
        fp = len(cuts) - tp
        precision = tp / len(cuts) if cuts else 0.0
        recall = tp / len(truth) if truth else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(f"    thr={thr:<5} 检出={len(cuts)} 命中={tp}/{len(truth)} "
              f"P={precision:.3f} R={recall:.3f} F1={f1:.3f}")
        if thr == 0.3:
            check("默认阈值下切分召回 = 1.0", recall == 1.0, f"R={recall:.3f}")
            check("默认阈值下无虚检", fp == 0, f"虚检 {fp} 个")


async def case_retrieval(materials: list[Path], entries: list[dict]) -> float:
    print("\n[案例 2] 真实素材上的中文检索（查询说法 ≠ 素材标签）")
    from clipwright.services import content_query as cq

    indexes = await build_indexes(materials)
    print(f"  素材：{len(indexes)} 段；总镜头 {sum(len(i.shots) for i, _s in indexes)} 个")
    for idx, cost in indexes:
        tags = []
        for s in idx.shots:
            tags.extend(s.visual.tags or [])
        print(f"    {Path(idx.filename).stem[:26]:28s} 镜头{len(idx.shots)} "
              f"标签={'/'.join(tags[:4]) or '（无）':22s} 建索引 {cost:.1f}s")

    pairs: list[tuple[str, str]] = []      # (中文查询, 期望素材文件名)
    for idx, _cost in indexes:
        entry = next((e for e in entries if e.get("file") == idx.filename), None)
        if not entry:
            continue
        for q in queries_for(entry):
            pairs.append((q, str(idx.filename)))

    if not pairs:
        print("  没有可评测的查询（检索词不在 QUERY_MAP 里）")
        return 0.0

    top1 = top3 = 0
    for q, want in pairs:
        res = await cq.search_shots(q, indexes=[i for i, _c in indexes],
                                    top_k=3, rerank=False)
        got = [h["filename"] for h in (res.get("hits") or [])]
        hit1 = bool(got) and got[0] == want
        hit3 = want in got
        top1 += 1 if hit1 else 0
        top3 += 1 if hit3 else 0
        mark = "TOP1" if hit1 else ("TOP3" if hit3 else "MISS")
        print(f"    [{mark}] 「{q}」 → {got[:3]}")
    n = len(pairs)
    random_top1 = 1 / max(1, len(indexes))
    print(f"\n  top-1 = {top1}/{n} = {top1 / n * 100:.1f}%（随机基线 {random_top1 * 100:.1f}%）")
    print(f"  top-3 = {top3}/{n} = {top3 / n * 100:.1f}%")
    check("真实素材 top-1 ≥ 70%", top1 / n >= 0.70, f"{top1 / n * 100:.1f}%")
    return top1 / n


async def case_pipeline(materials: list[Path], project_id: str) -> None:
    print("\n[案例 3] 管线贯通：问答 / 接地 / 一句话剪辑")
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services import content_index as ci
    from clipwright.services import content_query as cq
    from clipwright.services.intent_apply import plan_validate_apply
    from clipwright.services.scene_grounding import ground_scenes, validate_material_refs

    indexes = [await ci.build_index(m, force=True, mirror=False) for m in materials]

    qa = [
        ("这段素材里有哪些镜头？", "清单"),
        ("最长的镜头是哪个？", "排序"),
        ("有黑场的镜头吗？", "结构化过滤"),
        ("有哪些画面是运动的？", "结构化过滤"),
    ]
    for q, kind in qa:
        res = await cq.search_shots(q, indexes=indexes, top_k=2, rerank=False)
        hits = res.get("hits") or []
        print(f"    Q[{kind}] {q} → {len(hits)} 命中 "
              f"{[(h['ref'][:20], h['score']) for h in hits]}"
              f"{'' if hits else '  note=' + (res.get('note') or '')[:60]}")

    scenes = [{"title": "开场", "description": "交代环境的开场画面", "duration_sec": 5},
              {"title": "主体", "description": "面向观众的主体内容", "duration_sec": 8}]
    grounded, notes = ground_scenes(scenes, indexes)
    hits = sum(1 for s in grounded if s.get("material_refs"))
    print(f"    接地：{hits}/{len(grounded)} 个场景引用了真实镜头；"
          f"缺口 {'有' if any(s.get('material_gap') for s in grounded) else '无'}")
    problems = validate_material_refs(grounded, indexes)
    check("接地引用全部真实存在", not problems, str(problems)[:120])
    print(f"    （说明：{notes[0] if notes else '—'}）")

    clips, cursor = [], 0.0
    # id 必须唯一：**实测踩过**——按"素材名前 8 字符"生成 id 时
    # `pexels_3560017`/`pexels_3727447` 前 8 字符都是 `pexels_3`，三段时间线片段撞成一个 id，
    # 于是"删掉第2个片段"把三个一起删了（4 → 1）。这里用递增序号保证唯一。
    for idx in indexes:
        for i, shot in enumerate(idx.shots):
            dur = round(max(0.2, float(shot.duration_sec)), 3)
            clips.append(Clip(id=f"c{len(clips):03d}", kind="video",
                              asset_id=str(idx.asset_path), track_id="tr_v",
                              start_sec=round(cursor, 3), duration_sec=dur,
                              source_offset_sec=round(float(shot.t0), 3),
                              metadata={"source_file": idx.filename}))
            cursor += dur
    timeline = Timeline(id="tl_real", fps=float(indexes[0].fps or 25.0),
                        width=int(indexes[0].width or 640),
                        height=int(indexes[0].height or 360),
                        tracks=[Track(id="tr_v", name="V1", kind="video", index=0,
                                      clips=clips)])
    print(f"    时间线：{len(clips)} 个片段 / {timeline.total_duration_sec:.1f}s")
    for message in ("删掉第2个片段", "把所有片段速度调成1.5倍", "剪到10秒"):
        result = await plan_validate_apply(message, timeline=timeline, session_id="real",
                                           project_id=project_id, apply=True)
        if result.get("applied"):
            rep = result["report"]
            print(f"    「{message}」→ 已执行：{rep['duration_before']:.1f}s → "
                  f"{rep['duration_after']:.1f}s，片段 {rep['clips_before']} → "
                  f"{rep['clips_after']} 个")
            timeline = Timeline.model_validate(result["timeline"])
        else:
            why = (result.get("problems") or [{}])[-1].get("message", "")
            print(f"    「{message}」→ 未执行：{why[:90]}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/pexels_testset")
    ap.add_argument("--skip-pipeline", action="store_true")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        print(f"找不到 manifest：{manifest_path}（先跑 fetch_pexels_testset.py）")
        return 2
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    materials = [root / e["file"] for e in entries if (root / e["file"]).exists()]
    if not materials:
        print("素材目录里没有文件")
        return 2

    print("=" * 78)
    print(f"真实素材基准（Pexels · {len(materials)} 段）")
    print("来源与作者见 manifest.json；Pexels License 允许免费使用")
    print("=" * 78)

    from clipwright.services import content_index as ci

    workdir = Path(tempfile.mkdtemp(prefix="clipwright_realbench_"))
    # 索引写到临时库目录，避免污染真实素材库
    ci.settings.library_dir = workdir / "library"
    ci.settings.project_dir = workdir / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False

    try:
        await case_shot_detection(materials, workdir)
        recall = await case_retrieval(materials, entries)
        if not args.skip_pipeline:
            await case_pipeline(materials, "")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"检查项 {PASS}/{PASS + FAIL} 通过；检索 top-1 = {recall * 100:.1f}%")
    print("边界：素材为 Pexels 免费片段（多为单镜头、8s 级、低码率），"
          "检索查询为人工标注（说法刻意与标签不同）")
    print("=" * 78)
    print("REAL MATERIAL BENCH PASS" if not FAIL else "REAL MATERIAL BENCH FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
