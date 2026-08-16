"""P5-B3: LLM 成本预算熔断测试（monkeypatch 聚合函数，无 Mongo 依赖）。"""

from __future__ import annotations

import pytest

from clipwright.config import settings
from clipwright.services import budget


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    budget.reset_cache()
    yield
    budget.reset_cache()
    settings.llm_monthly_token_budget = 0


async def _fake_sum(monkeypatch, used: int):
    async def _sum() -> int:
        return used

    monkeypatch.setattr(budget, "_sum_month_tokens", _sum)


async def test_budget_disabled_allows(monkeypatch):
    settings.llm_monthly_token_budget = 0
    await _fake_sum(monkeypatch, 99999)
    allowed, _ = await budget.check_budget()
    assert allowed is True


async def test_budget_within_limit(monkeypatch):
    settings.llm_monthly_token_budget = 1000
    await _fake_sum(monkeypatch, 100)
    allowed, used = await budget.check_budget()
    assert allowed is True
    assert used == 100


async def test_budget_exceeded_denies(monkeypatch):
    settings.llm_monthly_token_budget = 10
    await _fake_sum(monkeypatch, 1000)
    allowed, used = await budget.check_budget()
    assert allowed is False
    assert used == 1000
