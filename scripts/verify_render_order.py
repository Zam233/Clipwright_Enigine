"""实拍式验收：渲染阶段的**真实执行顺序**与切点淡化（V1：video-use 硬规则 1 / 3）。

与单元测试的区别：这里真跑一遍 RenderService 全管线（合成源素材 + 转场 + 字幕 + MG +
PIP + 混音），然后从 `result.ffmpeg_log`（按执行顺序记录的 ffmpeg 命令）里取证：

1. **规则 1**：`ass=`（字幕/文字）命令必须出现在 MG `overlay` 与 PIP 之后；
2. **规则 3**：未显式设置淡化的音频片段带上 `afade=...:d=0.03`，
   显式设置过的片段（BGM 1.0s）保持原值不被覆盖；
3. 进度序列单调不减（prepare → … → text → audio → done）。

用法（工作目录 J:\\Clipwright，需 ffmpeg + Chromium）：
    python scripts/verify_render_order.py
退出码 0 = 全部断言通过。
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_smoke import build_timeline, probe_meta, synth_sources  # noqa: E402


def install_command_recorder() -> list[list[str]]:
    """记录真实执行的 ffmpeg 命令（按调用顺序）。

    `result.ffmpeg_log` 只含失败/告警注解，不含命令行；因此包装三个执行入口：
    异步 `_ff`、线程池 `_run_ff`、模块级 `run_tracked_ff`。
    """
    import clipwright.services.render as R

    recorded: list[list[str]] = []
    orig_ff = R.RenderService._ff
    orig_run_ff = R.RenderService._run_ff
    orig_tracked = R.run_tracked_ff

    async def rec_ff(self, cmd, **kw):
        recorded.append([str(c) for c in cmd])
        return await orig_ff(self, cmd, **kw)

    def rec_run_ff(self, cmd, **kw):
        recorded.append([str(c) for c in cmd])
        return orig_run_ff(self, cmd, **kw)

    def rec_tracked(cmd, **kw):
        recorded.append([str(c) for c in cmd])
        return orig_tracked(cmd, **kw)

    R.RenderService._ff = rec_ff          # type: ignore[assignment]
    R.RenderService._run_ff = rec_run_ff  # type: ignore[assignment]
    R.run_tracked_ff = rec_tracked        # type: ignore[assignment]
    return recorded


def _stage_of(cmd: list[str]) -> str | None:
    """把一条 ffmpeg 命令归类到合成阶段（用于顺序取证）。"""
    text = " ".join(cmd)
    low = text.lower()
    if "ass=" in low:
        return "text_ass"
    if "overlay=" in low and "enable=" in low:
        return "mg_overlay" if "zoompan" not in low else "pip_overlay"
    if "amix" in low or "afade" in low:
        return "audio"
    if "xfade" in low:
        return "concat"
    return None


async def main() -> int:
    failures: list[str] = []
    # 工作目录放在仓库内：ASS 字幕用「相对 CWD 的路径」传给 ffmpeg，
    # 若 work_dir 在系统临时目录（跨盘符）会退化成裸文件名 → ffmpeg 找不到文件。
    repo = Path(__file__).resolve().parents[1]
    use_temp = "--temp-work" in sys.argv
    if use_temp:
        work = Path(tempfile.mkdtemp(prefix="cw_verify_"))
    else:
        work = repo / "_cache" / "verify_render_order"
        if work.exists():
            import shutil

            shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    print(f"    work_dir={work}（--temp-work 用于验证跨目录/跨盘符场景）")

    # 清空视频阶段缓存：命中缓存会跳过 concat/MG/PIP/文本，无法取证顺序
    try:
        from clipwright.services.render import _VIDEO_CACHE_DIR

        for cached in _VIDEO_CACHE_DIR.glob("stage_*.mp4"):
            cached.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover
        print("    警告：清理阶段缓存失败", exc)
    try:
        print("[1/3] 合成源素材…")
        src = synth_sources(work)
        tl = build_timeline(src, with_mg=True)
        out = work / "verify_out.mp4"

        pcts: list[float] = []
        recorded = install_command_recorder()

        async def progress(stage, pct, msg):
            pcts.append(float(pct))

        from clipwright.schema.timeline import Timeline
        from clipwright.services.render import RenderService

        print("[2/3] 全管线渲染（含 MG + 字幕 + PIP + 音频）…")
        rs = RenderService(work_dir=work / "engine")
        result = await rs.render(Timeline(**tl), out, width=1280, height=720, fps=30,
                                 progress_callback=progress)
        if not result.success:
            print("渲染失败:", result.error)
            print("\n".join(result.ffmpeg_log.splitlines()[-30:]))
            return 1

        print("[3/3] 取证…")
        meta = probe_meta(out)
        print(f"    duration={meta['duration']:.2f}s video={meta['has_video']} audio={meta['has_audio']}")

        kinds = [k for k in (_stage_of(c) for c in recorded) if k]
        print(f"    阶段命中顺序: {kinds}")
        if "text_ass" not in kinds:
            failures.append("未捕获 ass= 字幕烧录命令")
        else:
            first_text = kinds.index("text_ass")
            if "mg_overlay" in kinds and kinds.index("mg_overlay") > first_text:
                failures.append("MG overlay 出现在字幕之后（规则 1 违例）")
            if "pip_overlay" in kinds and kinds.index("pip_overlay") > first_text:
                failures.append("PIP overlay 出现在字幕之后（规则 1 违例）")

        # 规则 3：30ms 切点淡化（narration 片段未显式设置淡化）
        all_cmds = " ".join(" ".join(c) for c in recorded)
        afades = re.findall(r"afade=t=(?:in|out):st=[\d.]+:d=([\d.]+)", all_cmds)
        print(f"    afade 值: {sorted(set(afades))}")
        if not any(abs(float(v) - 0.03) < 1e-6 for v in afades):
            failures.append(f"未发现 30ms 切点淡化（实际 afade={sorted(set(afades))}）")
        if not any(abs(float(v) - 1.0) < 1e-6 for v in afades):
            failures.append("显式设置的 1s BGM 淡化丢失（疑似被默认值覆盖）")
        if "字幕烧录批次" in result.ffmpeg_log:
            failures.append(f"字幕烧录失败: {result.ffmpeg_log[-200:]}")

        # 进度单调不减
        bad = [(a, b) for a, b in zip(pcts, pcts[1:]) if b < a]
        print(f"    进度序列: {[round(p, 1) for p in pcts]}")
        if bad:
            failures.append(f"进度回退: {bad[:3]}")
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)

    if failures:
        print("\nVERIFY FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nVERIFY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
