"""共享测试 fixtures。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_persona_dir() -> Path:
    return Path("personas/zam_knowledge_critical")
