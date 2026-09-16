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
    "soccer": ["球场的画面", "球员踢球的镜头", "足球比赛", "绿茵场上踢球"],
    "match": ["球场的画面", "球员踢球的镜头"],
    "ocean": ["海边的镜头", "海浪的画面", "大海", "浪花拍打礁石"],
    "waves": ["海浪的画面", "海边的镜头"],
    "beach": ["沙滩的画面", "海边的镜头", "阳光下的海滩"],
    "city": ["城市的街道", "夜里城市的街道", "高楼林立的城市画面"],
    "night": ["夜景画面", "夜里城市的街道", "晚上亮着灯的街道"],
    "traffic": ["街道上的车流", "城市交通"],
    "timelapse": ["城市延时画面", "城市的街道"],
    "cooking": ["做饭的镜头", "厨房里炒菜", "有人在做饭"],
    "kitchen": ["厨房里炒菜", "做饭的镜头"],
    "food": ["食物特写", "做饭的镜头", "一桌子菜的镜头"],
    "market": ["集市场景", "街道摊位的画面", "小摊上卖东西"],
    "forest": ["森林的画面", "树林里徒步", "绿树成荫的地方"],
    "hiking": ["树林里徒步", "森林的画面"],
    "dog": ["小狗在玩", "宠物的镜头", "狗在草地上跑"],
    "playing": ["小狗在玩", "宠物的镜头"],
}

#: 自然剪辑点的**视觉判读标注**（2026-09-14）。
#:
#: 真值来源从"三种像素算法一致"升级为**加一路独立的视觉判读**：把候选切点前后的帧
#: 拼成带时间标签的网格图，交给视觉模型判读"相邻帧之间有没有镜头切换"。
#: 关键是有**负对照**——不然"视觉模型永远说有切换"就没有信息量：
#: 两段单镜头素材（海浪 / 城市）都回答"没有镜头切换"，而狗片段回答
#: "有镜头切换，发生在 14.5s 与 14.7s 之间"（检测器给的是 14.75s，一致）。
#:
#: 仍然**不是人工逐帧标注**：判读者是视觉模型，不是人。
VISION_ANNOTATIONS: dict[str, dict] = {
    "pexels_35282483_640p.mp4": {"interval": (14.5, 14.7), "kind": "切换（正例）"},
    "pexels_6748729_426p.mp4": {"interval": None, "kind": "无切换（负例）"},
    "pexels_8064422_640p.mp4": {"interval": None, "kind": "无切换（负例）"},
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


async def case_natural_cut(materials: list[Path]) -> None:
    """自然剪辑点：**多方法一致 + 视觉判读标注**（仍非人工逐帧标注）。

    真值来源有三层，逐层加强：
    ① ffmpeg `scene` 滤镜（全帧像素差）；② 帧间平均绝对差尖峰 / 直方图跨度（另两套实现）；
    ③ **视觉判读**：把候选切点前后的帧拼成带时间标签的网格图，让视觉模型回答
    "相邻帧之间有没有镜头切换"。第 ③ 层才有"画面证据"的意思，而且**带负对照**
    （单镜头素材必须回答"没有切换"，否则该判读没有区分力）。
    """
    print("\n[案例 2] 真实素材的自然剪辑点（两路检测 + 视觉判读，非人工逐帧标注）")
    from clipwright.services import content_index as ci

    found: list[tuple[Path, list[float], tuple[list[float], list[float]]]] = []
    for m in materials:
        cuts = await ci.detect_cut_times(m, 0.3)
        strong, weak = await asyncio.to_thread(ci.detect_transition_candidates, m)
        if cuts or strong or weak:
            found.append((m, cuts, (strong, weak)))
    if not found:
        print("  这批素材里没有检出任何剪辑点（多为单镜头片段）——如实记录")
        check("自然剪辑点样本存在（否则该项未验证）", False, "本批素材无剪辑点")
        return

    import numpy as np

    # 逐段检查的对象要**包含"什么都没检测到"的负对照**——那才是判读有没有区分力的关键。
    # 踩坑：第一版只遍历 `found`（有信号的素材），于是负对照永远进不来检查。
    selected: list[Path] = [m for m in materials if m.name in VISION_ANNOTATIONS]
    selected += [m for m, _c, _s in found
                 if m.name not in VISION_ANNOTATIONS][:1]
    entries: list[tuple[Path, list[float], tuple[list[float], list[float]]]] = []
    for m in selected:
        cuts = await ci.detect_cut_times(m, 0.3)
        strong, weak = await asyncio.to_thread(ci.detect_transition_candidates, m)
        entries.append((m, cuts, (strong, weak)))

    agree_count = 0
    for m, cuts, (strong, weak) in entries:
        found_times = sorted({*[round(c, 2) for c in cuts], *strong, *weak})
        print(f"  {m.name}：切点 {[round(c, 2) for c in cuts][:4]}；"
              f"转场候选 强={strong} 弱={weak}")
        cap_diffs = _frame_diffs(m)
        spike_times: list[float] = []
        if cap_diffs:
            arr = np.asarray(cap_diffs, dtype="float32")
            p95 = float(np.percentile(arr, 95))
            spike_times = [round(i / 25.0, 2)
                           for i, d in enumerate(cap_diffs) if d > max(6.0, 2 * p95)]
        by_spike = [t for t in found_times
                    if any(abs(t - s) <= 0.5 for s in spike_times)]
        kind = "硬切" if by_spike else ("渐变" if (strong or weak) else "未确认")
        print(f"      第二路确认：帧间差尖峰 {spike_times[:5]} → {by_spike or '—'}")
        print(f"      → 该处自然转场类型：**{kind}**")

        annotation = VISION_ANNOTATIONS.get(m.name)
        if annotation:
            interval = annotation.get("interval")
            if interval is None:
                # 负对照的判据是"**没有被切成两个镜头**"：弱证据标注不算违反
                # （它正是"标注但不切"这个安全取舍的产物）。
                ok = not cuts and not strong
                print(f"      视觉判读：{annotation['kind']}（判读者：视觉模型）"
                      f" → {'一致' if ok else '**不一致**'}"
                      f"（切点 {[round(c, 2) for c in cuts]} / 强证据 {strong}"
                      f" / 弱证据标注 {weak}）")
            else:
                ok = any(interval[0] - 0.3 <= t <= interval[1] + 0.3 for t in found_times)
                print(f"      视觉判读：{annotation['kind']} 在 {interval[0]}–{interval[1]}s"
                      f" → 与检测{'一致' if ok else '**不一致**'}"
                      f"（检测 {found_times[:4]}）")
            check(f"{m.name} 检测与视觉判读一致", ok,
                  f"视觉={annotation} 检测={found_times[:4]}")
            agree_count += 1
        else:
            print("      视觉判读：该素材没有标注（未做网格判读）")
        check("自然转场被至少一种独立方法确认", kind != "未确认" or bool(annotation),
              f"切点 {found_times[:4]}")
    if agree_count:
        print(f"  已做视觉判读的素材：{agree_count} 段（含正例与负对照）")


def _frame_diffs(path: Path) -> list[float]:
    """帧间平均绝对差（与 ffmpeg scene 滤镜不同的实现，用于交叉验证）。"""
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(str(path))
        prev, out = None, []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype("float32")
            if prev is not None:
                out.append(float(np.mean(np.abs(gray - prev))))
            prev = gray
        cap.release()
        return out
    except Exception:  # noqa: BLE001
        return []


async def case_crossfade(materials: list[Path], workdir: Path) -> None:
    """交叉溶解（R2 记录的方法学盲区）：真值精确（xfade 的 offset 就是溶解起点）。"""
    print("\n[案例 3] 交叉溶解补检（真实素材 + xfade，真值精确）")
    from clipwright.services import content_index as ci

    if len(materials) < 2:
        print("  素材不足，跳过")
        return
    a, b = materials[0], materials[1]
    out = workdir / "real_xfade.mp4"
    da = float((await ci.probe_media(a)).get("duration") or 4.0)
    db = float((await ci.probe_media(b)).get("duration") or 4.0)
    dur = 0.6
    offset = max(1.0, min(da, db) - dur - 0.5)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b),
           "-filter_complex",
           f"[0:v]scale=640:360,setsar=1,fps=25,trim=0:{offset + dur},setpts=PTS-STARTPTS[v0];"
           f"[1:v]scale=640:360,setsar=1,fps=25,trim=0:{offset + dur},setpts=PTS-STARTPTS[v1];"
           f"[v0][v1]xfade=transition=fade:duration={dur}:offset={offset}[o]",
           "-map", "[o]", "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  合成溶接失败，跳过：{r.stderr[-160:]}")
        return
    truth_center = offset + dur / 2
    hard = await ci.detect_cut_times(out, 0.3, detect_gradual=False)
    strong, weak = await asyncio.to_thread(ci.detect_transition_candidates, out)
    combined = await ci.detect_cut_times(out, 0.3)
    print(f"  真值：溶解 {offset:.2f}–{offset + dur:.2f}s（中心 {truth_center:.2f}s）")
    print(f"  ffmpeg 单帧差异（老路径）: {[round(x, 2) for x in hard]}"
          f" → {'漏检（这就是盲区）' if not hard else '有检出'}")
    print(f"  两级补检：强（会切）={strong} 弱（只标注）={weak}")
    print(f"  合并后切点                : {[round(x, 2) for x in combined]}")
    # 口径："**发现**"= 切开 或 标注，两者都算补上了盲区（只不切是刻意的安全取舍）
    detected = combined + strong + weak
    hit = any(abs(t - truth_center) <= 0.9 for t in detected)
    check("交叉溶解被发现（切开或标注，补齐 R2 盲区）", hit,
          f"强={strong} 弱={weak} 合并={[round(x, 2) for x in combined]}")

    # 误报检查：无转场的真实片段上不该**切**（标注是允许的——那是"让人来判断"）
    false_splits = []
    for m in materials[:4]:
        s2, w2 = await asyncio.to_thread(ci.detect_transition_candidates, m)
        if s2:
            false_splits.append((m.name, s2))
    labelled = []
    for m in materials[:4]:
        _s2, w2 = await asyncio.to_thread(ci.detect_transition_candidates, m)
        if w2:
            labelled.append((m.name, w2))
    print(f"  误切检查（4 段无转场真实素材）：{false_splits or '无'}")
    print(f"  弱证据标注（允许，用于人工确认）：{labelled or '无'}")
    check("无转场素材的**误切**为 0（弱证据只标注）", not false_splits, str(false_splits))


async def case_cost(materials: list[Path], workdir: Path) -> None:
    """索引成本：真实像素上"每秒钟素材"的成本（本轮补的是**成本随长度**这一项）。"""
    print("\n[案例 4] 索引成本（真实像素，按时长缩放）")
    from clipwright.services import content_index as ci

    if not materials:
        return
    # 用真实素材循环拼一条长视频（内容重复，但**逐帧计算量真实**）
    #
    # 必须先逐段归一化再拼：`-f concat -c copy` 要求各段编码参数一致，
    # 而这批素材分辨率不同（426×240 / 640×360 / 426×178）——
    # 直接 copy 会得到一个**坏文件**（时长≈0、只切出 1 个镜头），
    # 实测第一版就踩了：报出"每秒素材 0ms"这种一眼假的数字。
    target_sec = 120.0
    norm_dir = workdir / "loop_norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    acc = 0.0
    i = 0
    while acc < target_sec:
        m = Path(materials[i % len(materials)])
        part = norm_dir / f"n{i}.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(m),
             "-vf", "scale=640:360:force_original_aspect_ratio=decrease,"
                    "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,format=yuv420p",
             "-an", "-c:v", "libx264", "-preset", "veryfast", str(part)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  归一化失败，跳过成本项：{r.stderr[-160:]}")
            return
        parts.append(part)
        acc += float((await ci.probe_media(m)).get("duration") or 0.0)
        i += 1
    listfile = workdir / "loop_list.txt"
    listfile.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts),
                        encoding="utf-8")
    long_video = workdir / "real_long.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listfile), "-c", "copy", str(long_video)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  拼接长素材失败，跳过：{r.stderr[-160:]}")
        return
    meta = await ci.probe_media(long_video)
    dur = float(meta.get("duration") or 0.0)
    if dur < 10:
        print(f"  长素材时长异常（{dur:.2f}s），成本项如实跳过（不报假数字）")
        return
    started = time.perf_counter()
    idx = await ci.build_index(long_video, force=True, mirror=False)
    cost = time.perf_counter() - started
    per_sec = cost / max(1e-6, dur)
    # 预热：**第一次**建索引会把 CLIP 权重加载（~15s）算进去，那是一次性成本，
    # 混进"每秒素材成本"里会让数字虚高一个量级。这里先把模型加载掉再计时。
    warm_started = time.perf_counter()
    try:
        from clipwright.services import content_vision as cvv

        await asyncio.to_thread(cvv._load_clip)
    except Exception as exc:  # noqa: BLE001
        print(f"  预热失败（成本数字会包含模型加载）：{exc}")
    _warm = time.perf_counter() - warm_started
    started = time.perf_counter()
    await ci.build_index(long_video, force=True, mirror=False)
    cost_warm = time.perf_counter() - started
    per_sec_warm = cost_warm / max(1e-6, dur)
    print(f"  素材：{dur:.1f}s / {meta.get('width')}×{meta.get('height')}（内容为真实片段循环，"
          "**计算量真实**）")
    print(f"  首次建索引（含模型加载）：{cost:.1f}s（{len(idx.shots)} 个镜头，"
          f"其中模型加载约 {max(0.0, cost - cost_warm):.1f}s，一次性）")
    print(f"  预热后建索引（稳态）    ：{cost_warm:.1f}s → **每秒素材 {per_sec_warm * 1000:.0f}ms**")
    print(f"  按稳态外推 10 分钟同分辨率素材 ≈ {per_sec_warm * 600 / 60:.1f} 分钟"
          "（**外推≠实测**；分辨率/码率不同成本不同，1080p 会明显更高）")
    check("索引成本已量化（真实像素、已剔除模型加载）", cost_warm > 0 and dur > 10,
          f"{per_sec_warm * 1000:.0f}ms/秒 / {dur:.0f}s 素材")


async def case_retrieval(materials: list[Path], entries: list[dict]) -> float:
    """中文检索：查询说法刻意不同于素材标签（字面匹配会失效的那种）。"""
    print("\n[案例 5] 真实素材上的中文检索（查询说法 ≠ 素材标签）")
    from clipwright.services import content_query as cq

    indexes = await build_indexes(materials)
    print(f"  素材：{len(indexes)} 段；总镜头 {sum(len(i.shots) for i, _s in indexes)} 个")
    for idx, cost in indexes:
        tags = []
        for s in idx.shots:
            tags.extend(s.visual.tags or [])
        print(f"    {Path(idx.filename).stem[:26]:28s} 镜头{len(idx.shots)} "
              f"标签={'/'.join(tags[:4]) or '（无）':22s} 建索引 {cost:.1f}s")

    # 期望素材用**可接受集合**而不是单一文件：同一个检索词会命中多条素材
    # （"city traffic night" 与 "city timelapse" 都是城市），拿单文件当唯一答案
    # 会把"正确但选了另一个城市镜头"算成错——第一版就是这么把 top-1 从 100% 压到 70% 的。
    accepted: list[tuple[str, set[str]]] = []
    for idx, _cost in indexes:
        entry = next((e for e in entries if e.get("file") == idx.filename), None)
        if not entry:
            continue
        q = str(entry.get("query") or "").lower()
        for zh in queries_for(entry):
            keys = {k for k in QUERY_MAP if k in q}
            ok_files = {str(e2.get("file")) for e2 in entries
                        if keys & {k for k in QUERY_MAP if k in str(e2.get("query") or "").lower()}}
            accepted.append((zh, ok_files))

    if not accepted:
        print("  没有可评测的查询（检索词不在 QUERY_MAP 里）")
        return 0.0

    top1 = top3 = 0
    for q, want in accepted:
        res = await cq.search_shots(q, indexes=[i for i, _c in indexes],
                                    top_k=3, rerank=False)
        got = [h["filename"] for h in (res.get("hits") or [])]
        hit1 = bool(got) and got[0] in want
        hit3 = any(g in want for g in got)
        top1 += 1 if hit1 else 0
        top3 += 1 if hit3 else 0
        mark = "TOP1" if hit1 else ("TOP3" if hit3 else "MISS")
        print(f"    [{mark}] 「{q}」 → {got[:3]}")
    n = len(accepted)
    random_top1 = 1 / max(1, len(indexes))
    print(f"\n  top-1 = {top1}/{n} = {top1 / n * 100:.1f}%（随机基线 {random_top1 * 100:.1f}%）")
    print(f"  top-3 = {top3}/{n} = {top3 / n * 100:.1f}%")
    check("真实素材 top-1 ≥ 70%", top1 / n >= 0.70, f"{top1 / n * 100:.1f}%")
    return top1 / n


async def case_pipeline(materials: list[Path], project_id: str) -> None:
    print("\n[案例 6] 管线贯通：问答 / 接地（具体描述 vs 泛描述）/ 一句话剪辑")
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

    scenes_concrete = [
        {"title": "夜色街道", "description": "夜里城市街道的画面", "duration_sec": 5},
        {"title": "海面", "description": "海浪拍岸的画面", "duration_sec": 5},
    ]
    scenes_generic = [
        {"title": "开场", "description": "交代环境的开场画面", "duration_sec": 5},
        {"title": "主体", "description": "面向观众的主体内容", "duration_sec": 8},
    ]
    for label, scenes in (("具体描述", scenes_concrete), ("泛描述", scenes_generic)):
        grounded, notes = ground_scenes(scenes, indexes)
        hits = sum(1 for s in grounded if s.get("material_refs"))
        print(f"    接地[{label}]：{hits}/{len(grounded)} 命中")
        for s in grounded:
            refs = s.get("material_refs") or []
            print(f"      · {s['title']}: {refs or ('缺口：' + (s.get('material_gap') or ''))[:70]}"
                  f"{'  匹配方式=' + str(s.get('material_match')) if refs else ''}")
        if label == "具体描述":
            check("具体描述能接地到真实镜头", hits >= 1, f"{hits}/{len(grounded)}")
        problems = validate_material_refs(grounded, indexes)
        check(f"接地引用全部真实存在（{label}）", not problems, str(problems)[:120])

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
    ap.add_argument("--skip-cost", action="store_true")
    ap.add_argument("--only-cost", action="store_true", help="只跑成本项（迭代标定用）")
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
        if args.only_cost:
            await case_cost(materials, workdir)
            recall = 0.0
        else:
            await case_shot_detection(materials, workdir)
            await case_natural_cut(materials)
            await case_crossfade(materials, workdir)
            if not args.skip_cost:
                await case_cost(materials, workdir)
            recall = await case_retrieval(materials, entries)
            if not args.skip_pipeline:
                await case_pipeline(materials, "")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"检查项 {PASS}/{PASS + FAIL} 通过；检索 top-1 = {recall * 100:.1f}%")
    print("边界：素材为 Pexels 免费片段（多为单镜头、低码率），检索查询为人工标注"
          "（说法刻意与标签不同）；自然剪辑点的可信依据是**三种独立方法一致**，"
          "**不是人工逐帧标注**（当前模型读不了图）")
    print("=" * 78)
    print("REAL MATERIAL BENCH PASS" if not FAIL else "REAL MATERIAL BENCH FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
