"""V17：类型同一性 **LLM 裁判**（PRD F12 的 L2 判定 + L3 反例检验）。

L1（``genre_conformance.evaluate_traits``）只用确定性指标，恒开、零成本。
但指标达标 ≠ 片子真是那个类型：3 秒一剪 + 满屏文字也可能是「口播切片」而
不是「歌词 MV」。这一层补的就是"气质"判断——**必须调真 LLM**，不许用指标
拼一个假的裁判结论（D7 的口径）。

开关（D7：默认关）
------------------
- 关：``check_conformance``/``acheck_conformance`` 直接写
  ``judge="skipped"``，绝不假装判过；
- 开：请求 payload 的 ``enable_genre_judge=true``，或服务端配置
  ``CLIPWRIGHT_GENRE_JUDGE_ENABLED=true``（真机取证时用后者，避免每次改请求）。

裁判输出（喂回 ``ConformanceReport.judge_detail``）
--------------------------------------------------
``{"mismatch": bool, "verdict": "match|mismatch", "confidence": 0..1,
  "reasons": [str], "evidence": [{"metric","value","expectation"}], "model": str}``

失败降级：LLM 异常/返回不可解析 → 抛给调用方，由 ``genre_conformance`` 统一
降级成 ``judge="skipped"`` + 原因（D6：裁判坏了不许阻断出片）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 裁判输出 JSON schema（结构化输出用；字段少而稳，便于真机解析）。
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mismatch": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "value": {"type": "number"},
                    "expectation": {"type": "string"},
                },
            },
        },
    },
    "required": ["mismatch"],
}

JUDGE_SYSTEM = (
    "你是短视频类型审核员。任务：判断给定的成片时间线**是否真是**声明的那种类型"
    "（同一性判定），而不是判断它「做得好看」。\n"
    "判定原则：\n"
    "1. 类型同一性看**结构性特征**：镜头时长分布、剪辑密度、文字/画面关系、"
    "节奏是否跟随音乐、镜头内容与主题的对应关系。\n"
    "2. L1 指标已达标也要复核——指标全过但形态明显是别的类型（例：满屏文字"
    "覆盖 + 3 秒一剪，看起来是口播切片而不是歌词 MV）必须判 mismatch。\n"
    "3. 反例检验（L3）：先假想一个**最接近但错误**的类型，说明为什么本片不是它；"
    "若无法区分，倾向判 match。\n"
    "4. 证据必须可核对：引用具体指标名与数值，禁止空话（「整体气质不符」这类）。\n"
    "只输出 JSON，不要 markdown 代码块。"
)


def _fmt_traits(traits: Iterable[Any], measured: dict[str, float]) -> str:
    lines: list[str] = []
    for trait in traits:
        name = getattr(trait, "name", "")
        mode = getattr(trait, "mode", "")
        value = getattr(trait, "value", 0.0)
        enforce = getattr(trait, "enforce", "")
        actual = measured.get(name)
        # L1 判定就地重算（trait.check 是纯函数）：裁判要看到"指标是否达标"的
        # 上下文，否则无法完成"指标都过了但形态不对"这类判定。
        passed: Optional[bool] = None
        if actual is not None and hasattr(trait, "check"):
            try:
                passed = bool(trait.check(float(actual)))
            except Exception:  # noqa: BLE001
                passed = None
        lines.append(
            f"- {name}: 要求 {mode} {value:g}（{enforce}），实测 "
            f"{'n/a' if actual is None else f'{actual:g}'}"
            + ("" if passed is None else f"，L1 判定 {'过' if passed else '不过'}")
        )
    return "\n".join(lines) or "- （该类型未声明指标）"


def build_user_prompt(
    type_id: str,
    traits: Iterable[Any],
    measured: dict[str, float],
    *,
    timeline_digest: Optional[dict[str, Any]] = None,
    type_hint: str = "",
) -> str:
    """拼裁判的用户提示：声明类型 + 指标 + 时间线摘要 + 反例要求。"""
    parts = [f"声明类型：{type_id}" + (f"（{type_hint}）" if type_hint else "")]
    parts.append("L1 指标：\n" + _fmt_traits(traits, measured))
    if measured:
        parts.append(
            "全部实测指标：\n"
            + json.dumps({k: round(float(v), 4) for k, v in measured.items()}, ensure_ascii=False)
        )
    if timeline_digest:
        parts.append("时间线摘要：\n" + json.dumps(timeline_digest, ensure_ascii=False, indent=2))
    parts.append(
        "请输出 JSON：{\"mismatch\": true/false, \"confidence\": 0-1, "
        "\"reasons\": [\"具体理由\"], \"evidence\": [{\"metric\": \"指标名\", "
        "\"value\": 数值, \"expectation\": \"该类型的应有表现\"}]}"
    )
    return "\n\n".join(parts)


def digest_timeline(timeline: Any, *, max_clips: int = 24) -> dict[str, Any]:
    """把时间线压成裁判能读的摘要（不吃 token、不含素材二进制）。

    分轨统计 ``video_clip_count`` / ``caption_clip_count``：只给一个总数会让裁判
    把字幕段也算成镜头（真机取证时确实发生过"8 个镜头被判成 16 段"）。
    """
    try:
        dur = float(getattr(timeline, "duration_sec", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        dur = 0.0
    clips: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for track in getattr(timeline, "tracks", []) or []:
        kind = str(getattr(track, "kind", "") or getattr(track, "type", ""))
        # pydantic Enum（ClipKind.VIDEO）取 .value 才是 "video"
        kind = str(getattr(track.kind, "value", kind)) if hasattr(track, "kind") else kind
        for clip in getattr(track, "clips", []) or []:
            counts[kind] = counts.get(kind, 0) + 1
            if len(clips) >= max_clips:
                continue
            clips.append(
                {
                    "track": kind,
                    "start": round(float(getattr(clip, "start_sec", 0.0) or 0.0), 2),
                    "dur": round(float(getattr(clip, "duration_sec", 0.0) or 0.0), 2),
                    "text": str(getattr(clip, "text", "") or "")[:60],
                    "anim": bool(getattr(clip, "animation", None)),
                }
            )
    return {
        "duration_sec": round(dur, 2),
        "clip_count": len(clips),
        "video_clip_count": counts.get("video", 0),
        "caption_clip_count": counts.get("caption", 0) + counts.get("text", 0),
        "clips": clips,
    }


def parse_judge_payload(raw: Any) -> dict[str, Any]:
    """规整裁判返回：容忍 markdown 围栏/字段缺失，只保证 ``mismatch`` 可信。"""
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001 - 不可解析一律抛错交给上层降级
            raise ValueError(f"裁判返回不是合法 JSON：{text[:200]}") from None
    if not isinstance(data, dict):
        raise ValueError(f"裁判返回类型非法：{type(data).__name__}")
    if "mismatch" not in data:
        raise ValueError(f"裁判返回缺 mismatch 字段：{list(data)[:6]}")

    mismatch = bool(data.get("mismatch"))
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reasons = data.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = []
    return {
        "mismatch": mismatch,
        "verdict": "mismatch" if mismatch else "match",
        "confidence": max(0.0, min(1.0, confidence)),
        "reasons": [str(r) for r in reasons if str(r).strip()],
        "evidence": [e for e in evidence if isinstance(e, dict)],
    }


async def llm_judge(
    traits: Iterable[Any],
    measured: dict[str, float],
    report: Any = None,
    *,
    type_id: str = "",
    timeline: Any = None,
    type_hint: str = "",
    llm: Any = None,
    use_flash: bool = False,
) -> dict[str, Any]:
    """真 LLM 裁判（async）。``judge`` 回调契约见 ``genre_conformance.acheck_conformance``。

    ``llm`` 可注入（单测传假实现）；缺省用 ``LLMService``。任何异常向上抛，
    由 ``acheck_conformance`` 降级为 ``judge="skipped"``。
    """
    if llm is None:
        from clipwright.services.llm import LLMService

        llm = LLMService()

    timeline_digest = digest_timeline(timeline) if timeline is not None else None
    user_prompt = build_user_prompt(
        type_id or getattr(report, "category_id", "") or "unknown",
        list(traits),
        measured,
        timeline_digest=timeline_digest,
        type_hint=type_hint,
    )
    logger.debug("类型裁判请求: type=%s, prompt=%.240s", type_id, user_prompt)

    if hasattr(llm, "structured_output"):
        raw = await llm.structured_output(
            JUDGE_SYSTEM, user_prompt, output_schema=JUDGE_SCHEMA, use_flash=use_flash
        )
    else:  # 兼容只有 chat() 的客户端
        text = await llm.chat(
            [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user_prompt}],
            temperature=0.0,
        )
        raw = text

    payload = parse_judge_payload(raw)
    payload["type_id"] = type_id
    payload["judged_by"] = "llm"
    payload["use_flash"] = bool(use_flash)
    model = getattr(llm, "model", "") or getattr(llm, "last_model", "")
    if model:
        payload["model"] = str(model)
    return payload


def judge_enabled(extra: Optional[dict[str, Any]] = None) -> bool:
    """裁判开关：显式 payload 开关 **或** 服务端配置（D7 默认关）。"""
    extra = extra or {}
    if "enable_genre_judge" in extra:
        return bool(extra.get("enable_genre_judge"))
    try:
        from clipwright.config import settings

        return bool(getattr(settings, "genre_judge_enabled", False))
    except Exception:  # noqa: BLE001 - 配置不可用按"关"处理
        return False


__all__ = [
    "JUDGE_SCHEMA",
    "JUDGE_SYSTEM",
    "build_user_prompt",
    "digest_timeline",
    "judge_enabled",
    "llm_judge",
    "parse_judge_payload",
]
