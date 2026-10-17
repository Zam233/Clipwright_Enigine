"""多方案生成 + 择优（P2-B）测试。

- `variants=1`（默认）必须与旧行为完全一致（直接委托 `generate`）；
- N>1 时并行发起（墙钟 ≈ 最慢一个，而非 N 倍），单候选异常不拖垮整批；
- 择优依据：视觉自评分 > LLM 自批判分 > 先到先得；
- 全部失败时返回失败结果，不抛异常。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from clipwright.animation.mg.generator import MGGenerator


def _gen() -> MGGenerator:
    return MGGenerator()


def _result(html: str, *, critique=None, visual=None, success=True) -> dict:
    return {
        "success": success, "html": html, "mg_def": {"elements": []},
        "method": "llm", "fallback_template": None, "generation_id": f"gen_{html}",
        "critique_score": critique, "visual_score": visual,
    }


class TestVariantCount:
    def test_default_is_single(self) -> None:
        assert _gen()._variant_count() == 1
        assert _gen()._variant_count(1) == 1

    def test_config_value_used(self) -> None:
        gen = _gen()
        gen._config["generation"]["variants"] = 3
        assert gen._variant_count() == 3

    def test_clamped_to_range(self) -> None:
        gen = _gen()
        assert gen._variant_count(99) == MGGenerator._MAX_VARIANTS
        assert gen._variant_count(0) == 1
        assert gen._variant_count(-5) == 1
        assert gen._variant_count("bad") == 1  # type: ignore[arg-type]


class TestVariantScore:
    def test_visual_preferred_over_critique(self) -> None:
        assert MGGenerator._variant_score({"visual_score": 88, "critique_score": 95}) == 88.0

    def test_falls_back_to_critique(self) -> None:
        assert MGGenerator._variant_score({"critique_score": 77}) == 77.0

    def test_missing_scores_are_zero(self) -> None:
        assert MGGenerator._variant_score({}) == 0.0
        assert MGGenerator._variant_score({"critique_score": None}) == 0.0


class TestGenerateBest:
    def _patch(self, gen: MGGenerator, payloads: list[dict], delay: float = 0.0,
               record: list[int] | None = None):
        inflight = {"now": 0, "peak": 0}
        counter = {"n": 0}

        async def _fake(**kwargs):
            # 索引必须在**任何 await 之前**同步取号，否则并发下三个候选会抢到同一个下标
            idx = counter["n"]
            counter["n"] += 1
            if record is not None:
                record.append(idx)
            inflight["now"] += 1
            inflight["peak"] = max(inflight["peak"], inflight["now"])
            try:
                if delay:
                    await asyncio.sleep(delay)
                payload = payloads[idx % len(payloads)]
                if isinstance(payload, Exception):
                    raise payload
                return dict(payload)
            finally:
                inflight["now"] -= 1

        gen.generate = _fake  # type: ignore[assignment]
        return inflight

    def test_single_variant_delegates(self) -> None:
        gen = _gen()
        calls: list[int] = []
        self._patch(gen, [_result("a")], record=calls)
        out = asyncio.run(gen.generate_best(description="d", text_content="t"))
        assert out["html"] == "a"
        assert "selected_index" not in out  # 单方案不加择优元数据
        assert len(calls) == 1

    def test_picks_highest_score(self) -> None:
        gen = _gen()
        payloads = [
            _result("low", critique=70),
            _result("high", critique=92),
            _result("mid", critique=80),
        ]
        self._patch(gen, payloads)
        out = asyncio.run(gen.generate_best(variants=3, description="d", text_content="t"))
        assert out["html"] == "high"
        assert out["selected_index"] == 1
        assert out["variants_considered"] == 3
        assert out["variant_scores"] == [70.0, 92.0, 80.0]

    def test_visual_score_wins(self) -> None:
        gen = _gen()
        payloads = [
            _result("critique_star", critique=95, visual=40),
            _result("visual_star", critique=60, visual=88),
        ]
        self._patch(gen, payloads)
        out = asyncio.run(gen.generate_best(variants=2, description="d", text_content="t"))
        assert out["html"] == "visual_star"

    def test_failures_are_isolated(self) -> None:
        gen = _gen()
        payloads = [RuntimeError("boom"), _result("survivor", critique=50)]
        self._patch(gen, payloads)
        out = asyncio.run(gen.generate_best(variants=2, description="d", text_content="t"))
        assert out["success"] is True and out["html"] == "survivor"

    def test_all_failed_returns_failure(self) -> None:
        gen = _gen()
        payloads = [_result("", success=False), _result("", success=False)]
        self._patch(gen, payloads)
        out = asyncio.run(gen.generate_best(variants=2, description="d", text_content="t"))
        assert out["success"] is False

    def test_runs_in_parallel(self) -> None:
        """3 个各 0.25s 的候选 → 墙钟应远小于串行的 0.75s。"""
        gen = _gen()
        inflight = self._patch(gen, [_result("a"), _result("b"), _result("c")], delay=0.25)
        t0 = time.time()
        asyncio.run(gen.generate_best(variants=3, description="d", text_content="t"))
        elapsed = time.time() - t0
        assert elapsed < 0.6, f"疑似串行执行: {elapsed:.2f}s"
        assert inflight["peak"] >= 2, "未见并发执行"

    def test_no_score_variants_pick_first_success(self) -> None:
        gen = _gen()
        self._patch(gen, [_result("first"), _result("second")])
        out = asyncio.run(gen.generate_best(variants=2, description="d", text_content="t"))
        assert out["html"] == "first"
        assert out["variant_scores"] == [0.0, 0.0]
