"""阶段结果合并（PRD §3.5）：语义与老编排器的 `_merge_agent_result` 一致。

原实现（老编排器 `_merge_agent_result`）：

- 非控制键并入共享结果；
- 控制键 `{timeline, agent_name, decision, error, status}` 排除（否则下游
  `script_skeleton` 会被灌进 `agent_name`/`decision` 之类垃圾）；
- `timeline` 以总线为准。

这里唯一新增的是"写入必须带归属"（`by=stage`），以便黑板做单向覆盖校验。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.services import task_blackboard as bb

#: 不许进黑板的控制键（沿用管线语义 + plugin payload）
CONTROL_KEYS: frozenset[str] = frozenset({
    "timeline", "agent_name", "decision", "error", "status", "payload",
})

#: 各阶段输出里可以进黑板的业务键（白名单，防止 Agent 私货污染下游）
MERGE_KEYS: dict[str, tuple[str, ...]] = {
    "structure": ("script_skeleton", "scenes"),
    "material": ("candidate_clips", "material_notes"),
    "edit": ("edit_notes",),
    "animation": ("animation_plan", "generated_mg_count"),
    "audio": ("audio_notes",),
    "quality": ("issues", "redo_agent"),
}


def payload_of(result: Any) -> dict[str, Any]:
    """Agent 输出 → 普通 dict（AgentOutputBase 或 dict）。"""
    if isinstance(result, dict):
        return dict(result)
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump(mode="json"))
        except TypeError:
            return dict(dump())
    return {}


def merge(stage: str, payload: dict[str, Any], *, task_id: str,
          allow: tuple[str, ...] | None = None) -> tuple[list[str], list[str]]:
    """把一次阶段输出合并进黑板；返回 `(写入的键, 合并错误)`。

    `timeline` 单独处理：它是渲染真值，允许的写入方向由黑板把关（edit→animation→audio），
    超过 2MB 时自动落盘转指针（否则会被静默丢弃，下游报"缺 timeline"）。
    合并错误必须回传给调用方（stage_subagent 会据此把 status 降为 degraded），
    只打 warning 等于让"时间线没存上"这件事消失。
    """
    keys = allow if allow is not None else MERGE_KEYS.get(stage, ())
    written: list[str] = []
    errors: list[str] = []

    data: dict[str, Any] = {}
    for key in keys:
        if key in CONTROL_KEYS or key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            continue
        data[key] = value

    for key, value in data.items():
        try:
            bb.put(task_id, key, value, by=stage)
            written.append(key)
        except bb.BlackboardError as exc:
            # 合并失败要可见，但不能让整个阶段失败（产物已经跑出来了）
            logger.warning("黑板书合并被拒绝 %s.%s: %s", task_id, key, exc)
            errors.append(f"{key}: {exc}")

    timeline = payload.get("timeline")
    # D6（PRD §19.5）：失败阶段的 timeline **不入库**。
    # 空壳时间线一旦写进黑板，下游 animation/audio 会基于空片继续跑，失败点被推得更远。
    _decision = str(payload.get("decision") or "").lower()
    if timeline and _decision in ("fail", "failed") and not (isinstance(timeline, dict)
                                                             and timeline.get("tracks")):
        logger.warning("跳过失败阶段的时间线合并 %s（by=%s）", task_id, stage)
        errors.append("timeline 未合并：阶段失败且时间线为空壳")
        timeline = None
    if timeline:
        value = timeline
        size = bb._size_of(value)  # noqa: SLF001 — 同族模块，复用大小口径
        if size > bb.MAX_BLACKBOARD_BYTES and not (
            isinstance(value, dict) and value.get("path")
        ):
            # 超大时间线：落盘 + 存指针（读端 `stage_inputs._as_timeline` 已支持）。
            # 旧行为是 put 抛错 → 只 warning → 时间线静默丢失，下游报"缺 timeline"。
            pointer = _spill_timeline(task_id, value)
            if pointer is not None:
                value = pointer
            else:
                errors.append(f"timeline 过大（{size / 1024 / 1024:.1f}MB）且落盘失败")
                return written, errors
        try:
            bb.put(task_id, "timeline", value, by=stage)
            written.append("timeline")
            if stage == "audio":
                # 音频是最后一道时间线生产者 → 同时定稿。
                # 定稿前必须过**确定性收尾**（类型 transform / 规范校验 / 片尾授权），
                # 这是 PRD §4.4 的保留不变量：编排可以交给模型，领域规范不行。
                from clipwright.services import timeline_finalize

                plugin = None
                try:
                    from clipwright.category.registry import CategoryRegistry

                    plugin = CategoryRegistry.get(str(payload.get("category_plugin_id") or ""))
                except Exception:  # noqa: BLE001 — 拿不到插件就只做片尾
                    plugin = None
                finalized = timeline_finalize.finalize(
                    value if isinstance(value, dict) else None,
                    plugin=plugin,
                    extra_params=dict(payload.get("extra_params") or {}),
                    pid=task_id,
                )
                bb.put(task_id, "final_timeline", finalized, by=stage)
                written.append("final_timeline")
        except bb.BlackboardError as exc:
            logger.warning("时间线合并被拒绝 %s（by=%s）: %s", task_id, stage, exc)
            errors.append(f"timeline: {exc}")

    return written, errors


def _spill_timeline(task_id: str, timeline: dict) -> dict | None:
    """把超大时间线写到磁盘，返回 `{path, sha256, bytes}` 指针；失败返回 None。"""
    try:
        import hashlib
        import json

        from clipwright.services.workspace import Workspace, workspace_root

        base = workspace_root()
        ws = Workspace(task_id)
        root = ws.dirs().root if ws.exists else base / "_tasks" / str(task_id)[-12:]
        out_dir = root / "cache"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "timeline_spill.json"
        text = json.dumps(timeline, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return {"path": str(path),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "bytes": len(text.encode("utf-8"))}
    except Exception as exc:  # noqa: BLE001 — 落盘失败由调用方降级
        logger.warning("时间线落盘失败 %s: %s", task_id, exc)
        return None


__all__ = ["CONTROL_KEYS", "MERGE_KEYS", "merge", "payload_of"]
