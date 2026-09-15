"""R1 验收脚本：素材内容索引（镜头级）端到端。

跑什么：
1. 合成一段"硬切"视频（纯色段 + 一段闪白碎片 + 一段运动噪声）；
2. 建索引 → 打印镜头表（时间范围/亮度/锐度/运动/黑场）；
3. 再建一次 → 验证**缓存命中**（不重算）；
4. 篡改内容（重新合成不同长度）→ 验证 **sha 变化触发重建**；
5. 换模型指纹 → 验证**旧索引被判失效**（避免新旧模型字段混在一份索引里）；
6. 打印摘要（喂 LLM 的形态）与覆盖率。

用法：
    python scripts/verify_content_index.py [--keep]
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

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.schema.content import INDEX_VERSION  # noqa: E402
from clipwright.services import content_index as ci  # noqa: E402


def _make_video(path: Path, segments: list[tuple[str, float]], fps: int = 10) -> None:
    """合成硬切视频：每段 (颜色, 秒数)。`noise` 表示带颗粒的灰（用于制造帧间差）。"""
    inputs: list[str] = []
    for i, (color, sec) in enumerate(segments):
        if color == "noise":
            src = (f"nullsrc=s=160x120:r={fps}:d={sec},"
                   f"geq=random(1)*255:128:128,format=yuv420p")
            inputs += ["-f", "lavfi", "-i", src]
        else:
            inputs += ["-f", "lavfi", "-i", f"color=c={color}:s=160x120:r={fps}:d={sec}"]
    filters = "".join(f"[{i}:v]" for i in range(len(segments)))
    filters += f"concat=n={len(segments)}:v=1:a=0[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", filters,
           "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"合成测试视频失败: {r.stderr[-400:]}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("需要本机 ffmpeg/ffprobe")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="clipwright_content_index_"))
    # 素材库/项目目录指到临时目录，绝不污染真实数据
    ci.settings.library_dir = tmp / "library"
    ci.settings.project_dir = tmp / "projects"
    ci.settings.library_dir.mkdir(parents=True, exist_ok=True)
    ci.settings.content_index_mirror_mongo = False

    failures: list[str] = []
    try:
        video = tmp / "sample.mp4"
        _make_video(video, [
            ("black", 1.2),      # 黑场
            ("red", 1.0),
            ("white", 1.0),
            ("blue", 1.0),
            ("noise", 1.0),      # 噪声 → 帧间差（运动信号）
            ("green", 1.0),
        ])
        size_mb = video.stat().st_size / 1e6
        print(f"[1] 合成素材 {video.name}: {size_mb:.2f}MB")

        t0 = time.perf_counter()
        idx = await ci.build_index(video, force=True, mirror=False)
        build_sec = time.perf_counter() - t0
        print(f"[2] 建索引 {idx.build_ms}ms（外层 {build_sec:.2f}s）→ "
              f"{len(idx.shots)} 镜头 / {idx.duration_sec}s / {idx.width}x{idx.height}@{idx.fps:g}fps")
        print(f"    sha={idx.sha256[:12]} 指纹={idx.model_fingerprint} 版本={idx.index_version}")
        print("    镜头表：")
        for s in idx.shots:
            flags = []
            if s.stats.is_black:
                flags.append("黑场")
            if s.stats.is_white:
                flags.append("白场")
            print(f"      s{s.index:<2d} [{s.t0:5.2f}-{s.t1:5.2f}] "
                  f"亮度{s.stats.brightness:6.1f} 对比{s.stats.contrast:5.1f} "
                  f"锐度{s.stats.sharpness:8.1f} 运动{s.stats.motion:5.1f} "
                  f"镜头={s.visual.camera_motion:<7s} {' '.join(flags)}")

        # 断言：切分合理 + 黑场被识别 + 噪声段运动最大
        if len(idx.shots) < 5:
            failures.append(f"镜头数偏少（{len(idx.shots)} < 5）：硬切没被切开")
        if not idx.shots[0].stats.is_black:
            failures.append("首段纯黑未被识别为黑场")
        white = [s for s in idx.shots if s.t1 <= 3.3 and s.t0 >= 2.0]
        if not white or not white[0].stats.is_white:
            failures.append("第二段纯白未被识别为白场")
        all_motion = [s.stats.motion for s in idx.shots if s.stats.frame_count >= 2]
        noise_shot = max(idx.shots, key=lambda s: s.stats.motion)
        others = [s.stats.motion for s in idx.shots if s is not noise_shot]
        if not others or noise_shot.stats.motion < 20.0 or noise_shot.stats.motion <= max(others) * 5:
            failures.append(
                f"噪声段运动未显著高于其它段：max={noise_shot.stats.motion:.1f} "
                f"others={[round(m, 1) for m in others]}")
        if noise_shot.visual.camera_motion != "high":
            failures.append(f"噪声段镜头运动分类应为 high，实得 {noise_shot.visual.camera_motion}")

        print("[3] 缓存与失效：")
        second = await ci.build_index(video, mirror=False)
        print(f"    同素材再建 → served_from_cache={second.served_from_cache} "
              f"build_ms={second.build_ms}（应为 True / 与首次相同）")
        if not second.served_from_cache:
            failures.append("同材料重复建索引未命中缓存")

        # 换指纹 → 判为失效（模拟换模型）
        stale = ci.load_index(idx.sha256)
        assert stale is not None
        stale.model_fingerprint = "ffffffffffff"
        if ci.index_is_fresh(stale, idx.sha256, idx.model_fingerprint):
            failures.append("模型指纹不同仍被判为新鲜索引")
        print(f"    换模型指纹 → index_is_fresh={ci.index_is_fresh(stale, idx.sha256, idx.model_fingerprint)}（应为 False）")

        # 内容变化 → sha 变化 → 不同索引文件
        video2 = tmp / "sample2.mp4"
        _make_video(video2, [("red", 2.0), ("blue", 2.0)])
        idx2 = await ci.build_index(video2, force=True, mirror=False)
        print(f"    另一段素材 sha={idx2.sha256[:12]}（与首段不同：{idx2.sha256 != idx.sha256}）")
        if idx2.sha256 == idx.sha256:
            failures.append("不同内容得到相同 sha")
        if len(ci.local_index_files()) < 2:
            failures.append("索引文件数不足（内容寻址未落盘两份）")

        print("[4] 摘要（喂 LLM 的形态）：")
        for line in idx.summary_text(max_shots=6).splitlines():
            print(f"    {line}")
        cov = idx.coverage()
        print(f"[5] 覆盖率: " + " ".join(f"{k}={v:.2f}" for k, v in cov.items()))
        if cov["stats"] < 1.0:
            failures.append(f"统计覆盖率不足：{cov['stats']}")

        print("[6] 本地索引总览:", json.dumps(ci.summarize_index_files(), ensure_ascii=False))

        print()
        if failures:
            print("CONTENT INDEX VERIFY FAIL")
            for f in failures:
                print("  -", f)
            return 1
        print("CONTENT INDEX VERIFY PASS")
        print(f"（索引目录：{ci.index_dir()}；版本 {INDEX_VERSION}）")
        return 0
    finally:
        if args.keep:
            print(f"[keep] 临时目录保留：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
