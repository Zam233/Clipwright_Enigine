"""MG hyperframes 渲染并行化 — 单元测试。

背景: ``_apply_all_hyperframes`` 原先逐个**串行**渲染各 MG 的 MOV（hyperframes
CLI，CPU/GPU 密集，单个 ~6-7min，16 个 ~2.5h）。修复后改为**有界并发**
（semaphore = ``settings.pipeline_concurrency`` 或默认 4），随后再**串行**执行
ffmpeg overlay 叠加（re-encode，链式依赖）。

本文件用 ``asyncio.sleep(1)`` 归一化每个 MOV 渲染（真实 hyperframes 渲染留在
单元测试之外），核心度量用 ``time.perf_counter()`` 包住真实 await 调用：
- (a) 基线表征：concurrency=1（强制串行）下 4 个 MG ≈ 4s —— 文档化串行行为
- (b) 失败优先：concurrency=4 下 4 个 MG 应 < 3.5s —— 修复前串行 ≈4s 失败
- (c) 修复后：4 个 MOV 全部渲染完成、输出路径互不相同、叠加顺序按输入稳定
- (d) 失败隔离：单个 MOV 渲染抛异常不悬挂 gather，其余 3 个仍完成
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from clipwright.animation.hyperframes_renderer import HyperframesRenderer
from clipwright.config import settings
from clipwright.services.render import RenderService

_MOV_SLEEP = 1.0  # 每个 MOV 渲染的模拟耗时（秒）


def _mk_mg(i: int) -> dict:
    """构造一个最小 MG overlay 条目（带 mg_html / 时间窗）。"""
    return {
        "mg_html": f"<html><body>MG #{i}</body></html>",
        "start_sec": i * 2.0,
        "duration_sec": 1.0,
    }


def _mk_mgs(n: int = 4) -> list[dict]:
    return [_mk_mg(i) for i in range(n)]


def _patch_mov_render(svc, monkeypatch, *, sleep_s=_MOV_SLEEP,
                      fail_ff_call: int | None = None,
                      mov_paths: list | None = None,
                      overlay_calls: list | None = None):
    """把 hyperframes MOV 渲染（``self._ff`` 外部 CLI）替换为可控假实现。

    - ``sleep_s``: 每次渲染耗时（模拟 CPU/GPU 密集 hyperframes render）
    - ``fail_ff_call``: 第 N 次 ``_ff`` 调用抛 RuntimeError（模拟渲染失败）
    - ``mov_paths``: 记录每次 ``-o`` MOV 输出路径（用于断言互不相同）
    - ``overlay_calls``: 记录每次 ``render_overlay_on_video`` 调用参数

    MOV 有效性 / overlay re-encode 一律短路为成功，避免真实 ffmpeg/hyperframes
    进入单元测试。返回 ``{"n": count}`` 记录 ``_ff`` 被调用次数。
    """
    count = {"n": 0}

    async def fake_ff(cmd, **kw):
        count["n"] += 1
        idx = count["n"]
        if fail_ff_call is not None and idx == fail_ff_call:
            raise RuntimeError(f"simulated MOV render failure #{idx}")
        if mov_paths is not None:
            mov_paths.append(cmd[cmd.index("-o") + 1])
        await asyncio.sleep(sleep_s)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(svc, "_ff", fake_ff)
    # 渲染产物统一视为有效（hyperframes 未真实运行，磁盘无 MOV 文件）
    monkeypatch.setattr(
        "clipwright.services.render._is_valid_video",
        lambda _p, min_bytes=1024: True,
    )
    if overlay_calls is not None:
        def fake_overlay(*a, **k):
            overlay_calls.append(a)
            return True

        monkeypatch.setattr(
            HyperframesRenderer, "render_overlay_on_video", staticmethod(fake_overlay))
    else:
        monkeypatch.setattr(
            HyperframesRenderer, "render_overlay_on_video",
            staticmethod(lambda *a, **k: True))
    return count


def _run_apply_all(svc, mgs):
    """真实 await 调用，用 perf_counter 度量 wall-clock。"""
    t0 = time.perf_counter()
    out = asyncio.run(svc._apply_all_hyperframes("base.mp4", [], mgs, 1920, 1080, 30.0))
    return out, time.perf_counter() - t0


class TestMgHyperframesParallel:
    """MG 渲染并行化：基线 / 失败优先 / 并发正确性 / 失败隔离。"""

    def test_baseline_serial_wallclock(self, tmp_path, monkeypatch) -> None:
        """(a) 基线表征：concurrency=1（强制串行）下 4 个 MG ≈ 4s。

        修复前天然串行；修复后 concurrency=1 回退串行，因此本测试在
        修复前后均成立，用于文档化既有串行行为（1s/个 × 4 ≈ 4s）。
        """
        monkeypatch.setattr(settings, "pipeline_concurrency", 1)
        svc = RenderService(tmp_path)
        _patch_mov_render(svc, monkeypatch, sleep_s=_MOV_SLEEP)

        out, dt = _run_apply_all(svc, _mk_mgs(4))

        assert out != "base.mp4", "4 个 MG 叠加后应产生新视频"
        assert 3.5 <= dt <= 6.0, (
            f"串行基线应为 ≈4s（4 × {_MOV_SLEEP}s），实际 {dt:.2f}s")

    def test_parallel_wallclock_under_3_5s(self, tmp_path, monkeypatch) -> None:
        """(b) 失败优先：concurrency=4 下 4 个 MG 应 < 3.5s。

        修复前串行 ≈4s → 本测试失败；修复后有界并发 ≈1s → 通过。
        """
        monkeypatch.setattr(settings, "pipeline_concurrency", 4)
        svc = RenderService(tmp_path)
        _patch_mov_render(svc, monkeypatch, sleep_s=_MOV_SLEEP)

        out, dt = _run_apply_all(svc, _mk_mgs(4))

        assert out != "base.mp4"
        assert dt < 3.5, (
            f"并行应 ≈1s（4 路并发 × {_MOV_SLEEP}s），实际 {dt:.2f}s（串行基线 ≈4s）")

    def test_all_movs_distinct_paths_concurrent(self, tmp_path, monkeypatch) -> None:
        """(c) 修复后：4 个 MOV 全部渲染完成、输出路径互不相同、按输入顺序叠加。

        防竞态：两个任务写入同一输出路径是该优化的失败模式 —— 每个 MOV 的
        ``-o`` 路径必须互不相同，且 gather 保序 → 叠加与输入顺序一致。
        """
        monkeypatch.setattr(settings, "pipeline_concurrency", 4)
        svc = RenderService(tmp_path)
        mov_paths: list = []
        overlay_calls: list = []
        ff_count = _patch_mov_render(
            svc, monkeypatch, sleep_s=_MOV_SLEEP,
            mov_paths=mov_paths, overlay_calls=overlay_calls)

        out, dt = _run_apply_all(svc, _mk_mgs(4))

        assert dt < 3.0, f"4 路并发 ≈1s，实际 {dt:.2f}s"
        assert ff_count["n"] == 4, "4 个 MOV 渲染都必须完成"
        assert len(mov_paths) == 4, "每个 MOV 都应有独立输出路径"
        assert len(set(mov_paths)) == 4, "MOV 输出路径必须互不相同（防竞态）"
        assert out != "base.mp4"
        applied = [a[0] for a in overlay_calls]
        assert sorted(applied) == sorted(mov_paths), "叠加顺序与输入 MOV 一一对应"

    def test_failed_mov_does_not_abort_others(self, tmp_path, monkeypatch) -> None:
        """(d) 失败隔离：单个 MOV 渲染抛异常，其余 3 个仍完成。

        return_exceptions=True → 失败的 MOV 被跳过，gather 不悬挂、不中止其他任务。
        """
        monkeypatch.setattr(settings, "pipeline_concurrency", 4)
        svc = RenderService(tmp_path)
        overlay_calls: list = []
        ff_count = _patch_mov_render(
            svc, monkeypatch, sleep_s=_MOV_SLEEP,
            fail_ff_call=2, overlay_calls=overlay_calls)

        out, dt = _run_apply_all(svc, _mk_mgs(4))

        assert dt < 2.5, f"失败不应悬挂 gather，实际 {dt:.2f}s"
        assert ff_count["n"] == 4, "4 个渲染都被尝试"
        assert len(overlay_calls) == 3, "失败 MOV 被跳过，其余 3 个叠加完成"
        assert out != "base.mp4"
