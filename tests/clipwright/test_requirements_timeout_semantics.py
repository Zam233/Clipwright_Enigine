"""超时语义回归：`0 = 不设超时`，不能被直接喂给 `asyncio.wait_for`。

背景（2026-10-29 线上症状）：前端提示「后端暂不可达，无法生成简报」。
真因是 `/api/requirements/chat` 把 `CHAT_HARD_TIMEOUT = 0` 直接传给
`asyncio.wait_for(..., timeout=0)` —— 0 秒立即取消，接口秒回 504，前端统一
显示成"后端不可达"。`/api/requirements/edit-timeline` 同病。
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from clipwright.api import requirements as req_api
from clipwright.services import timeouts


@pytest.mark.asyncio
async def test_wait_or_run_zero_means_unlimited():
    async def slow():
        await asyncio.sleep(0.05)
        return "done"

    t0 = time.monotonic()
    assert await timeouts.wait_or_run(slow(), 0) == "done"
    assert time.monotonic() - t0 >= 0.04, "0 不应被当作立即超时"


@pytest.mark.asyncio
async def test_wait_or_run_positive_timeout_still_raises():
    async def slow():
        await asyncio.sleep(0.2)

    with pytest.raises(asyncio.TimeoutError):
        await timeouts.wait_or_run(slow(), 0.01)


def test_chat_endpoint_uses_wait_or_run():
    src = inspect.getsource(req_api)
    assert "wait_or_run(" in src, "chat/edit 应走统一超时语义"
    # 反例：不能再把常量直接传给 wait_for
    assert "timeout=CHAT_HARD_TIMEOUT" not in src
    assert "timeout=EDIT_HARD_TIMEOUT" not in src


def test_hard_timeout_constants_are_unlimited():
    src = inspect.getsource(req_api)
    assert "CHAT_HARD_TIMEOUT = 0" in src
    assert "EDIT_HARD_TIMEOUT = 0" in src


def test_resolve_treats_zero_and_none_as_unlimited():
    assert timeouts.resolve(0) is None
    assert timeouts.resolve(None) is None
    assert timeouts.resolve(-5) is None
    assert timeouts.resolve(0.5) == 0.5
    assert timeouts.limited(0) is False
    assert timeouts.limited(30) is True
