"""类型同一性校验（PRD F12 / A-09）。

三级设计（PRD 已拍板 D7）：

- **L1 可测代理**：确定性计算 ``signature_traits``，**恒开、零 LLM 成本**；
- **L2 LLM 裁判** / **L3 反例检验**：默认关闭（``enable_genre_judge=False``），
  关闭时结果里必须显式写 ``judge: "skipped"``，不允许"看起来通过了"。

判定口径：
- ``enforce == "strict"`` 的指标不达标 → ``ok=False``，触发一次定向重做；
- ``soft`` 不达标 → 记入 ``warnings``，不阻断；
- ``type_mismatch`` 只在 L2/L3 开启时才会产生（否则无法判定"气质"）。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from clipwright.schema.category_spec import SignatureTrait

#: 判定状态。
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIP = "skipped"


class TraitResult(BaseModel):
    name: str
    mode: str
    threshold: float
    value: float
    passed: bool
    enforce: str
    reason: str = ""

    def render(self) -> str:
        verdict = "过" if self.passed else "不过"
        return f"{self.name}={self.value:g}（{self.mode} {self.threshold:g}，{self.enforce}）{verdict}"


class ConformanceReport(BaseModel):
    """一次同一性校验的完整结果（可进 ``prompt_resolution``）。"""

    ok: bool = True
    traits: list[TraitResult] = Field(default_factory=list)
    judge: str = STATUS_SKIP  # skipped / passed / mismatch
    judge_detail: dict[str, Any] = Field(default_factory=dict)
    mismatches: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def failed_strict(self) -> list[TraitResult]:
        return [t for t in self.traits if not t.passed and t.enforce == "strict"]

    def summary(self) -> str:
        passed = sum(1 for t in self.traits if t.passed)
        head = f"类型符合度 {passed}/{len(self.traits)}"
        if not self.ok:
            head += "（不通过）"
        tail = "；未做 LLM 同一性复核" if self.judge == STATUS_SKIP else f"；裁判={self.judge}"
        return head + tail


def evaluate_traits(
    traits: Iterable[SignatureTrait],
    measured: dict[str, float],
) -> list[TraitResult]:
    """L1：逐条指标判定（纯函数）。"""
    results: list[TraitResult] = []
    for trait in traits:
        value = float(measured.get(trait.name, 0.0))
        passed = trait.check(value)
        threshold = float(trait.value)
        results.append(
            TraitResult(
                name=trait.name,
                mode=trait.mode,
                threshold=threshold,
                value=value,
                passed=passed,
                enforce=trait.enforce,
                reason=trait.reason,
            )
        )
    return results


def check_conformance(
    traits: Iterable[SignatureTrait],
    measured: dict[str, float],
    *,
    enable_genre_judge: bool = False,
    judge: Optional[Any] = None,
) -> ConformanceReport:
    """完整校验：L1 恒开；L2/L3 由 ``enable_genre_judge`` 控制。

    ``judge`` 是可选回调（签名 ``judge(traits, measured, report) -> dict``），
    由调用方注入真实 LLM 裁判；**未注入时不允许假装判过**。
    """
    traits = list(traits)
    results = evaluate_traits(traits, measured)
    report = ConformanceReport(traits=results)

    for result in results:
        if result.passed:
            continue
        if result.enforce == "strict":
            report.mismatches.append(f"{result.name} 未达标：{result.render()}")
        else:
            report.warnings.append(f"{result.name} 未达标（soft，不阻断）：{result.render()}")

    report.ok = not any(t.enforce == "strict" and not t.passed for t in results)

    if not enable_genre_judge:
        report.judge = STATUS_SKIP
        report.judge_detail = {"reason": "D7：LLM 同一性裁判默认关闭（L1 指标仍已计算）"}
        return report

    if judge is None:
        report.judge = STATUS_SKIP
        report.judge_detail = {"reason": "已开启裁判但未注入 judge 回调"}
        return report

    try:
        payload = judge(traits, measured, report) or {}
    except Exception as exc:  # noqa: BLE001 - 裁判失败不得阻断出片（D6）
        report.judge = STATUS_SKIP
        report.judge_detail = {"reason": f"裁判异常，已降级：{exc}"}
        return report

    report.judge = "mismatch" if payload.get("mismatch") else "passed"
    report.judge_detail = dict(payload)
    if report.judge == "mismatch" and payload.get("reasons"):
        report.mismatches.extend(str(r) for r in payload["reasons"])
        report.ok = False
    return report


__all__ = [
    "ConformanceReport",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_SKIP",
    "TraitResult",
    "check_conformance",
    "evaluate_traits",
]
