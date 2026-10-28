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
