"""R10 端到端 Demo：真实素材 → 建索引 → 内容问答 → 方案接地 → 一句话剪辑。

**这个脚本是有真实素材时最该跑的脚本**——R1–R9 的验收脚本都用合成数据，
这里跑的是同一条真实链路：

    素材(视频) → 内容索引(L0 统计 + R2 画面 + R3 音频 + R9 视觉向量)
              → 内容问答(R4 检索 + R9 语义)
              → 方案接地(R8：规划书引用真实镜头)
              → 一句话剪辑(R5–R7：意图 → 落位 → 补丁 → dry-run → 执行)

用法：

    # 真实素材（可给多个，构成一个真实素材库）
    python scripts/demo_content_understanding.py `
        --material data\\pexels_testset\\a.mp4 --material data\\pexels_testset\\b.mp4 `
        --question "有哪些画面是运动的？" --question "找海边的镜头"

    # 没有素材时合成一段（只验证链路通不通，不代表真实效果）
    python scripts/demo_content_understanding.py

**这个脚本不会假装**：每一步都把"实际拿到了什么/没拿到什么"打印出来
（没索引就是没索引、没语音就是没语音、CLIP 不可用就说不可用、
描述与查询对不上就直接显示对不上）。真实素材上跑出什么就是什么——这才是 demo 的意义。
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

DEFAULT_QUESTIONS = (
    "这段素材里有哪些镜头？",
    "最长的镜头是哪个？",
    "有黑场吗？",
    "有哪些画面是运动的？",
)

DEFAULT_MESSAGES = (
    "删掉第2个片段",
    "把所有片段速度调成1.5倍",
    "把这个视频变得更有感染力一点",
)


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


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


async def step_index(materials: list[Path], workdir: Path, label: bool):
    hr("① 内容索引：AI 到底看到了什么")
    from clipwright.services import content_index as ci

    ci.settings.library_dir = workdir / "library"
    ci.settings.project_dir = workdir / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False
    if not label:
        ci.settings.content_index_clip_enabled = False
        ci.settings.content_index_visual_llm = False

    indexes = []
    for material in materials:
        print(f"\n--- {material.name} ---")

        def progress(p: float, message: str) -> None:
            print(f"    [{p * 100:5.1f}%] {message}")

        started = time.perf_counter()
        index = await ci.build_index(material, force=True, mirror=False, progress=progress)
        cost = time.perf_counter() - started
        indexes.append(index)

        print(f"  素材：{index.filename}（{index.duration_sec:.1f}s / "
              f"{index.width}×{index.height} / {index.fps:.2f}fps）")
        print(f"  镜头：{len(index.shots)} 个；建索引耗时 {cost:.1f}s")
        print(f"  产出维度（sources）：{index.sources}")
        print(f"  维度覆盖率：{index.coverage()}")
        print("  逐镜头：")
        for shot in index.shots:
            desc = (shot.visual.description or "").strip() or "（无描述）"
            tags = "/".join((shot.visual.tags or [])[:3]) or "（无标签）"
            labels = "/".join((shot.visual.labels or [])[:2]) or "（无英文标签）"
            speech = (shot.speech.text or "").strip()[:24] or "（无语音）"
            vec = "有向量" if (shot.embeddings or {}).get("clip") else "无向量"
            print(f"    #{shot.index} {shot.t0:6.2f}-{shot.t1:6.2f}s "
                  f"亮度{shot.stats.brightness:5.1f} 锐度{shot.stats.sharpness:7.1f} "
                  f"运动{shot.visual.camera_motion:7s} 景别{shot.visual.shot_size:13s} "
                  f"人脸{str(shot.visual.has_face):5s}")
            print(f"       描述：{desc[:60]}")
            print(f"       标签：{tags:26s} 英文：{labels:30s} 语音：{speech}  {vec}")
    return indexes


async def step_query(project_id: str, questions: list[str]):
    hr("② 内容问答：用自然语言问「素材里有什么」")
    from clipwright.services.content_query import search_shots

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
            print(f"        镜头文字：{(hit.get('text') or '')[:70]}")


async def step_grounding(indexes, project_id: str):
    hr("③ 方案接地：规划书里的场景能不能钉到真实镜头")
    from clipwright.services.scene_grounding import ground_scenes, validate_material_refs

    scenes = [
        {"title": "开场", "description": "交代环境的开场画面", "duration_sec": 5},
        {"title": "主体", "description": "面向观众的主体内容", "duration_sec": 8},
        {"title": "结尾", "description": "收尾画面", "duration_sec": 3},
    ]
    grounded, notes = ground_scenes(scenes, indexes)
    for scene in grounded:
        refs = scene.get("material_refs") or []
        gap = scene.get("material_gap") or ""
        print(f"  · {scene['title']}: {refs or ('缺口：' + gap)}")
        if refs:
            print(f"      匹配方式={scene.get('material_match')} "
                  f"分数={scene.get('material_scores')}")
    problems = validate_material_refs(grounded, indexes)
    print(f"\n  引用不存在的素材：{len(problems)} 条{problems if problems else '（0 = 通过）'}")
    print("  说明：" + "；".join(notes[:3]))


async def step_nl_edit(indexes, project_id: str, messages: list[str]):
    hr("④ 一句话剪辑：意图 → 落位 → 补丁 → dry-run → 执行")
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services.intent_apply import plan_validate_apply

    video_clips = []
    cursor = 0.0
    for index in indexes:
        stem = Path(index.filename).stem[:12]
        for i, shot in enumerate(index.shots):
            dur = round(max(0.2, float(shot.duration_sec)), 3)
            # **顺序摆放**：每个素材的镜头时间戳都是从 0 起算的（各自素材内的时间），
            # 直接拿它当时间线位置会让所有素材叠在同一段上——实测踩过：
            # 4 段素材全落在 0-8s，删除一个片段后总时长反而从 8.1s 涨到 24.2s
            # （收拢逻辑遇到重叠时会把后续片段往右推）。
            video_clips.append(Clip(
                id=f"c_{stem}_{i}", kind="video", asset_id=str(index.asset_path),
                track_id="tr_v", start_sec=round(cursor, 3), duration_sec=dur,
                source_offset_sec=round(float(shot.t0), 3)))
            cursor += dur

    def _new_timeline(pairs) -> "Timeline":
        return Timeline(
            id="tl_demo",
            fps=float(indexes[0].fps or 30.0) if indexes else 30.0,
            width=int(indexes[0].width or 1920) if indexes else 1920,
            height=int(indexes[0].height or 1080) if indexes else 1080,
            tracks=[Track(id="tr_v", name="V1", kind="video", index=0, clips=list(pairs))])

    timeline = _new_timeline(video_clips)
    print(f"  时间线：{len(video_clips)} 个片段，总长 {timeline.total_duration_sec:.1f}s")

    for message in messages:
        preview = await plan_validate_apply(message, timeline=timeline, session_id="demo",
                                            project_id=project_id)
        print(f"\n  指令：「{message}」")
        first = (preview.get("intent") or "").splitlines()
        print(f"    解析：{first[0] if first else '（未解析出）'}")
        for line in (preview.get("report") or {}).get("preview", [])[:4]:
            print(f"    dry-run：{line}")
        problems = preview.get("problems") or []
        if problems:
            print(f"    未执行原因：{problems[-1].get('message', '')[:110]}")
        elif preview.get("blocked"):
            print("    未执行：需要用户先回答澄清问题")
        else:
            result = await plan_validate_apply(message, timeline=timeline, session_id="demo",
                                               project_id=project_id, apply=True)
            if result.get("applied"):
                report = result["report"]
                print(f"    已执行：总时长 {report['duration_before']:.1f}s → "
                      f"{report['duration_after']:.1f}s，"
                      f"片段 {report['clips_before']} → {report['clips_after']} 个")
                timeline = Timeline.model_validate(result["timeline"])
            else:
                print("    未执行（执行阶段被拦下）")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--material", action="append", default=[],
                    help="真实素材路径（可重复；不给则合成一段）")
    ap.add_argument("--no-label", action="store_true", help="跳过画面标注（不加载 CLIP，快）")
    ap.add_argument("--question", action="append", default=[],
                    help="内容问答（可重复；默认内置 4 条）")
    ap.add_argument("--message", action="append", default=[],
                    help="自然语言剪辑指令（可重复；默认内置 3 条）")
    ap.add_argument("--keep", action="store_true", help="保留临时索引目录")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("需要本机 ffmpeg/ffprobe")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="clipwright_demo_"))
    try:
        if args.material:
            materials = [Path(p) for p in args.material]
            missing = [p for p in materials if not p.exists()]
            if missing:
                print(f"素材不存在：{missing}")
                return 2
            print(f"使用真实素材：{[p.name for p in materials]}")
        else:
            material = workdir / "synthetic.mp4"
            make_synthetic(material)
            materials = [material]
            print("没有给 --material，使用**合成素材**（只验证链路，不代表真实效果）")

        indexes = await step_index(materials, workdir, label=not args.no_label)
        await step_query("", list(args.question) or list(DEFAULT_QUESTIONS))
        await step_grounding(indexes, "")
        await step_nl_edit(indexes, "", list(args.message) or list(DEFAULT_MESSAGES))

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
