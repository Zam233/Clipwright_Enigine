"""共享测试 fixtures。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 模型权重都已在本机 HF 缓存里；但每次加载仍会先发 HEAD 探网，网络抖动会让
# RAG/CLIP 相关用例随机失败（实测 SSL UNEXPECTED_EOF_WHILE_READING）。
# 测试不该依赖外网 → 默认离线（缓存在则不联网，缺失则明确报错而不是挂 60s）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# `tests/` 自身也进 sys.path：测试内的共享桩（如 kernel_stubs）按裸模块名导入，
# 不依赖包相对导入（`tests` 是包，直接 import tests.kernel_stubs 会和 -k 选择冲突）。
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def sample_persona_dir() -> Path:
    return Path("personas/zam_knowledge_critical")


@pytest.fixture(autouse=True)
def _disable_kernel_recovery(monkeypatch, request):
    """单测默认关闭 Kernel 确定性补派。

    它会真的调 `surface.execute("delegate_agent")`（生产语义）；在单测里那是
    真实派发/联网，会把用例拖成超时。要测补派的用例用
    `@pytest.mark.enable_kernel_recovery` 显式打开。
    """
    from clipwright.config import settings

    enabled = bool(request.node.get_closest_marker("enable_kernel_recovery"))
    monkeypatch.setattr(settings, "agent_kernel_recovery", enabled, raising=False)
    yield
