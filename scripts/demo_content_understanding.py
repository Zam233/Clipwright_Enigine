"""R10 端到端 Demo：一段素材 → 建索引 → 内容问答 → 方案接地 → 一句话剪辑。

**这个脚本是有真实素材时唯一值得跑的脚本**——前面 R1–R9 的验收脚本都用合成数据，
这里跑的是同一条真实链路：

    素材(视频) → 内容索引(L0 统计 + R2 画面 + R3 音频 + R9 视觉向量)
              → 内容问答(R4 检索 + R9 语义)
              → 方案接地(R8：规划书引用真实镜头)
              → 一句话剪辑(R5–R7：意图 → 落位 → 补丁 → dry-run → 执行)

用法：

    # 用真实素材（推荐；每次跑到"AI 在剪什么"都会打印实际索引出来的东西）
    python scripts/demo_content_understanding.py --material D:\\footage\\match.mp4

    # 没有素材时合成一段（颜色硬切，只用于验证链路通不通）
    python scripts/demo_content_understanding.py

**这个脚本不会假装**：每一步都把"实际拿到了什么/没拿到什么"打印出来
（没索引就是没索引、没语音就是没语音、CLIP 不可用就说不可用）。
真实素材上跑出什么结果就是什么结果——这才是 demo 的意义。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def make_synthetic(path: Path) -> None:
    """合成 6 段硬切视频（黑/红/白/蓝/噪声/绿）——只在没有真实素材时用。"""
    segments = [("black", 1.2), ("red", 1.0), ("white", 1.0),
                ("blue", 1.0), ("noise", 1.0), ("green", 1.0)]
    inputs: list[str] = []
    for color, sec in segments:
        if color == "noise":
            src = "nullsrc=s=320x240:r=10:d=1.0,geq=random(1)*255:128:128,format=yuv420p"
        else:
            src = f"color=c={color}:s=320x240:r=10:d={sec}"
        inputs += ["-f", "lavfi", "-i", src]
    filt = "".join(f"[{i}:v]" for i in range(len(segments)))
    filt += f"concat=n={len(segments)}:v=1:a=0[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", filt,
           "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"合成素材失败: {r.stderr[-400:]}")


async def step_index(material: Path, workdir: Path, label: bool):
    hr("① 内容索引：AI 到底看到了什么")
    from clipwright.services import content_index as ci

    ci.settings.library_dir = workdir / "library"
    ci.settings.project_dir = workdir / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False
    if not label:
        ci.settings.content_index_clip_enabled = False
        ci.settings.content_index_visual_llm = False

    def progress(p: float, message: str) -> None:
        print(f"    [{p * 100:5.1f}%] {message}")

    started = time.perf_counter()
    index = await ci.build_index(material, force=True, mirror=False, progress=progress)
    cost = time.perf_counter() - started

    print(f"\n  素材：{index.filename}（{index.duration_sec:.1f}s / "
          f"{index.width}×{index.height} / {index.fps:.2f}fps）")
    print(f"  镜头：{len(index.shots)} 个；建索引耗时 {cost:.1f}s"
          f"{'（含模型加载）' if cost > 20 else ''}")
    print(f"  产出维度（sources）：{index.sources}")
    coverage = index.coverage()
    print(f"  维度覆盖率：{coverage}")
    print("\n  逐镜头（最多 12 个）：")
    for shot in index.shots[:12]:
        desc = (shot.visual.description or "").strip() or "（无描述）"
        tags = "/".join(shot.visual.tags[:3]) or "（无标签）"
        speech = (shot.speech.text or "").strip()[:20] or "（无语音）"
        vec = "有向量" if (shot.embeddings or {}).get("clip") else "无向量"
        print(f"    #{shot.index} {shot.t0:6.2f}-{shot.t1:6.2f}s "
              f"亮度{shot.stats.brightness:5.1f} 锐度{shot.stats.sharpness:6.1f} "
              f"运动{shot.visual.camera_motion:7s} 景别{shot.visual.shot_size:13s} "
              f"| {desc[:24]:24s} | {tags:20s} | {speech:20s} | {vec}")
    return index


async def step_query(index, project_id: str):
    hr("② 内容问答：用自然语言问「这段素材里有什么」")
    from clipwright.services.content_query import search_shots

    questions = [
        "这段素材里有哪些镜头？",
        "最长的镜头是哪个？",
        "有黑场吗？",
        "有哪些画面是运动的？",
    ]
    for q in questions:
        result = await search_shots(q, project_id=project_id, top_k=3)
        hits = result.get("hits") or []
        print(f"\n  Q: {q}")
        if not hits:
            print(f"    → 没有命中（{result.get('note') or '检索无结果'}）")
            continue
        for hit in hits:
            print(f"    → {hit['ref']}  {hit['t0']:.2f}-{hit['t1']:.2f}s  "
                  f"score={hit['score']}  {'；'.join(hit.get('why') or [])}")


async def step_grounding(index, material: Path, project_id: str):
    hr("③ 方案接地：规划书里的场景能不能钉到真实镜头")
    from clipwright.services.scene_grounding import ground_scenes, validate_material_refs

    scenes = [
        {"title": "开场", "description": "画面开场，交代环境", "duration_sec": 5},
        {"title": "主体", "description": "画面主体内容", "duration_sec": 8},
        {"title": "结尾", "description": "收尾画面", "duration_sec": 3},
    ]
    grounded, notes = ground_scenes(scenes, [index])
    for scene in grounded:
        refs = scene.get("material_refs") or []
        gap = scene.get("material_gap") or ""
        print(f"  · {scene['title']}: {refs or ('缺口：' + gap)}"
              f"{'  匹配方式=' + str(scene.get('material_match')) if refs else ''}")
    problems = validate_material_refs(grounded, [index])
    print(f"\n  引用不存在的素材：{len(problems)} 条{problems if problems else '（0 = 通过）'}")
    print("  说明：" + "；".join(notes[:3]))


async def step_nl_edit(index, project_id: str):
    hr("④ 一句话剪辑：意图 → 落位 → 补丁 → dry-run → 执行")
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services.intent_apply import plan_validate_apply

    # 把索引里的镜头摆成一条时间线（模拟"素材已经剪进来了"）
    video_clips = [
        Clip(id=f"c{i}", kind="video", asset_id=str(index.asset_path), track_id="tr_v",
             start_sec=round(float(shot.t0), 3),
             duration_sec=round(max(0.2, float(shot.duration_sec)), 3),
             source_offset_sec=round(float(shot.t0), 3))
        for i, shot in enumerate(index.shots)
    ]
    timeline = Timeline(
        id="tl_demo", fps=index.fps or 30.0, width=index.width or 1920,
        height=index.height or 1080,
        tracks=[Track(id="tr_v", name="V1", kind="video", index=0, clips=video_clips)],
    )
    print(f"  时间线：{len(video_clips)} 个片段，总长 {timeline.total_duration_sec:.1f}s")

    messages = ["删掉第2个片段", "把所有片段速度调成1.5倍", "把这个视频变得更有感染力一点"]
    for message in messages:
        preview = await plan_validate_apply(message, timeline=timeline, session_id="demo",
                                            project_id=project_id)
        print(f"\n  指令：「{message}」")
        print(f"    解析：{preview['intent'].splitlines()[0] if preview['intent'] else '（未解析出）'}")
        for line in (preview.get("report") or {}).get("preview", [])[:4]:
            print(f"    dry-run：{line}")
        problems = preview.get("problems") or []
        if problems:
            print(f"    未执行原因：{problems[-1].get('message', '')[:100]}")
        elif preview.get("blocked"):
            print("    未执行：需要用户先回答澄清问题")
        else:
            result = await plan_validate_apply(message, timeline=timeline, session_id="demo",
                                               project_id=project_id, apply=True)
            if result.get("applied"):
                new = result["timeline"]
                print(f"    已执行：总时长 {result['report']['duration_before']:.1f}s → "
                      f"{result['report']['duration_after']:.1f}s，"
                      f"片段 {result['report']['clips_before']} → "
                      f"{result['report']['clips_after']} 个")
                timeline = Timeline.model_validate(new)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--material", default="", help="真实素材路径（视频）；不给则合成一段")
    ap.add_argument("--no-label", action="store_true", help="跳过画面标注（不加载 CLIP，快）")
    ap.add_argument("--keep", action="store_true", help="保留临时索引目录")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("需要本机 ffmpeg/ffprobe")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="clipwright_demo_"))
    try:
        if args.material:
            material = Path(args.material)
            if not material.exists():
                print(f"素材不存在：{material}")
                return 2
            print(f"使用真实素材：{material}")
        else:
            material = workdir / "synthetic.mp4"
            make_synthetic(material)
            print("没有给 --material，使用**合成素材**（只验证链路，不代表真实效果）")

        index = await step_index(material, workdir, label=not args.no_label)
        await step_query(index, project_id="")
        await step_grounding(index, material, project_id="")
        await step_nl_edit(index, project_id="")

        hr("完成")
        print("说明：本 demo 用同一份索引贯通「看得见 → 答得上 → 引用得到 → 改得动」四步。")
        print("没有任何索引维度时，对应步骤会如实打印缺口，不会编造结果。")
        return 0
    finally:
        if args.keep:
            print(f"\n临时目录保留在：{workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
