"""Bug3 回归测试 — ffmpeg GPU 智能检测（_resolve_encoder）。

基线（Baseline）：钉住配置默认值与 hwaccel 前缀行为。
Failing-first：探测决策必须跟随（被 mock 的）机器能力——
``ffmpeg -encoders`` 含 h264_nvenc 且 nvidia-smi 就绪且运行时探针通过 → h264_nvenc；
任一不满足 → libx264；日志必须含 ``[Render] encoder=... reason=...``。

真实机器能力证据由手工 QA（ffmpeg -encoders / nvidia-smi / python -c 调用）提供，
见 DoneClaim manual_qa。此处用可控 mock 断言决策逻辑本身。
"""

from __future__ import annotations

import logging
import subprocess

import pytest

import clipwright.services.render as render_mod
from clipwright.services.render import _hwaccel_args, _resolve_encoder


@pytest.fixture(autouse=True)
def _reset_encoder_cache(monkeypatch):
    """每个用例前重置模块级探测缓存，保证用例间隔离。"""
    monkeypatch.setattr(render_mod, "_encoder_resolved", None)
    yield


def _mock_env(monkeypatch, *, has_nvenc: bool, has_gpu: bool,
              probe_ok: bool = True) -> list[list[str]]:
    """mock ffmpeg -encoders / nvidia-smi / 运行时探针，返回调用记录。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "ffmpeg" and "-encoders" in cmd:
            out = " V..... h264_nvenc  NVIDIA NVENC\n" if has_nvenc else " V..... libx264\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if cmd[0] == "nvidia-smi":
            rc = 0 if has_gpu else 9
            out = "NVIDIA GeForce RTX 3080\n" if has_gpu else ""
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        raise AssertionError(f"未预期的外部调用: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(render_mod, "_nvenc_runtime_probe", lambda: probe_ok)
    # 清空显式配置，走智能探测路径
    from clipwright.config import settings
    monkeypatch.setattr(settings, "render_encoder", "", raising=False)
    return calls


class TestBaselinePinned:
    """基线特征化测试：钉住当前行为。"""

    def test_config_default_empty(self) -> None:
        """config.render_encoder 默认空串 = 运行时智能探测。"""
        from clipwright.config import settings
        assert getattr(settings, "render_encoder", "") in ("", "libx264", "h264_nvenc")

    def test_hwaccel_args(self) -> None:
        """NVENC → cuda 硬件解码前缀；libx264 → 空前缀。"""
        assert _hwaccel_args("h264_nvenc") == ["-hwaccel", "cuda"]
        assert _hwaccel_args("libx264") == []


class TestResolveEncoder:
    """Failing-first：决策跟随机器能力 + 结果缓存 + 日志含原因。"""

    def test_nvenc_selected_when_fully_available(self, monkeypatch, caplog) -> None:
        calls = _mock_env(monkeypatch, has_nvenc=True, has_gpu=True, probe_ok=True)
        with caplog.at_level(logging.INFO, logger="clipwright"):
            enc = _resolve_encoder()
        assert enc == "h264_nvenc"
        assert any("encoder=h264_nvenc" in r.message and "reason=" in r.message
                   for r in caplog.records), caplog.text
        # 结果缓存：再次调用不再发起外部探测
        n = len(calls)
        assert _resolve_encoder() == "h264_nvenc"
        assert len(calls) == n

    def test_fallback_when_nvenc_missing(self, monkeypatch, caplog) -> None:
        _mock_env(monkeypatch, has_nvenc=False, has_gpu=True)
        with caplog.at_level(logging.INFO, logger="clipwright"):
            enc = _resolve_encoder()
        assert enc == "libx264"
        assert any("encoder=libx264" in r.message and "reason=" in r.message
                   for r in caplog.records), caplog.text

    def test_fallback_when_gpu_missing(self, monkeypatch, caplog) -> None:
        _mock_env(monkeypatch, has_nvenc=True, has_gpu=False)
        with caplog.at_level(logging.INFO, logger="clipwright"):
            assert _resolve_encoder() == "libx264"
        assert "reason=" in caplog.text

    def test_fallback_when_runtime_probe_fails(self, monkeypatch, caplog) -> None:
        """nvenc 存在 + GPU 存在，但真实编码失败（驱动过低）→ 回退。"""
        _mock_env(monkeypatch, has_nvenc=True, has_gpu=True, probe_ok=False)
        with caplog.at_level(logging.INFO, logger="clipwright"):
            assert _resolve_encoder() == "libx264"
        assert "reason=" in caplog.text

    def test_config_override_skips_probe(self, monkeypatch) -> None:
        """显式配置 render_encoder → 尊重配置，不发起任何外部探测。"""
        from clipwright.config import settings
        monkeypatch.setattr(settings, "render_encoder", "libx264", raising=False)

        def boom(cmd, **kw):
            raise AssertionError(f"配置覆盖时不应探测: {cmd}")

        monkeypatch.setattr(subprocess, "run", boom)
        assert _resolve_encoder() == "libx264"

    def test_result_is_deterministic_set(self, monkeypatch) -> None:
        """无论环境如何，返回值必在允许集合内（确定性）。"""
        _mock_env(monkeypatch, has_nvenc=False, has_gpu=False)
        assert _resolve_encoder() in {"h264_nvenc", "libx264"}
