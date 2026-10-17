"""clipwright 测试包的共享隔离。

背景（2026-09-13 排查到）：Provider 源存储默认落在 `anchor("provider_sources.json")`，
即仓库根目录的真实数据文件。若开发机上跑过应用（或某个用例触发了
`ensure_migrated()`），该文件会带着 main/flash/vision 的角色绑定存在——于是
`LLMService._build_client()` 会被绑定覆盖，导致「设置 settings.llm_provider 后断言
provider 类型」这类单测出现环境相关的失败。

这里做**全包级隔离**：每个用例的 Provider 源存储都重定向到临时目录，用例结束复位，
保证单测永不读写真实数据文件。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_provider_sources(tmp_path):
    from clipwright.services import provider_sources as ps

    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)
