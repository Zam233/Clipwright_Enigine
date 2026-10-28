"""最终时间线的确定性收尾（modern-agent PRD §4.2 第 2 步 / §4.4「保留的确定性模块」）。

老编排器被删之前，这些逻辑长在它的 `_finalize_timeline` 里：类型插件 transform、
`validate_clip` 规范校验、片尾素材授权名单。它们是**领域不变量**，不是编排顺序——
模型可以选择调什么工具、什么时候调，但不能选择跳过类型规范校验或伪造片尾授权。

所以这里把它们原样搬成中立模块（`pipeline_v2` 已物理删除），由 `stage_merge` 在
"最终时间线"落黑板时调用一次。
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Callable, Optional

from clipwright.config import logger

#: 片尾授权名单时长（秒）与最多列出的来源数（超出折叠为"等 N 项"）
CREDIT_DUR_SEC = 2.5
CREDIT_MAX_SOURCES = 4


def collect_credit_lines(final_tl: dict) -> tuple[list[str], int]:
    """从时间线 clip 元数据里收集去重后的授权名单，返回 `(行列表, 去重来源数)`。"""
    seen: set[tuple] = set()
    rows: list[str] = []
    for tr in final_tl.get("tracks", []) or []:
        for c in tr.get("clips", []) or []:
            meta = c.get("metadata") or {}
            lic = str(meta.get("license") or "").strip()
            if not lic:
                continue
            author = str(meta.get("author") or "").strip()
            title = str(meta.get("source_title") or lic).strip()
            key = (lic, author)
            if key in seen:
                continue
            seen.add(key)
            rows.append(f"{title}（{author}，{lic}）" if author else f"{title}（{lic}）")
    return rows, len(seen)


def append_credit_outro(final_tl: dict, credits: tuple[list[str], int]) -> dict:
    """在时间线末尾追加 `CREDIT_DUR_SEC` 秒的片尾字幕（不改原对象）。"""
    rows, source_count = credits
    shown = rows[:CREDIT_MAX_SOURCES]
    if len(rows) > CREDIT_MAX_SOURCES:
        shown.append(f"等共 {len(rows)} 项，详见项目素材信息")
    text = "素材来源\n" + "\n".join(shown)

    tl = copy.deepcopy(final_tl)
    cap_track = next((t for t in tl.get("tracks", []) if t.get("kind") == "caption"), None)
    if cap_track is None:
        cap_track = {"id": f"t_credit_{uuid.uuid4().hex[:6]}", "name": "字幕",
                     "kind": "caption", "index": len(tl.get("tracks", [])), "clips": []}
        tl.setdefault("tracks", []).append(cap_track)
    if not cap_track.get("clips"):
        cap_track["clips"] = []
    has_clips = any(t.get("clips") for t in tl.get("tracks", []))
    end = max(
        (float(c.get("start_sec", 0) or 0) + float(c.get("duration_sec", 0) or 0))
        for t in tl.get("tracks", []) for c in (t.get("clips", []) or [])
    ) if has_clips else 0.0
    cap_track["clips"].append({
        "id": f"cc_credit_{uuid.uuid4().hex[:6]}",
        "kind": "caption",
        "asset_id": "",
        "track_id": cap_track["id"],
        "start_sec": round(end, 3),
        "duration_sec": CREDIT_DUR_SEC,
        "text": text,
        "font_size": 30,
        "position": "center",
        "renderer": "ass",
        "metadata": {"category": "credit", "source_count": source_count},
    })
    tl["duration_sec"] = round(float(tl.get("duration_sec", 0) or 0) + CREDIT_DUR_SEC, 3)
    return tl


def _validate_clips(final_tl: dict, plugin: Any) -> int:
    """按类型插件的 `validate_clip` 校验视频 clip；返回违规数（并打标）。"""
    validator = getattr(plugin, "validate_clip", None)
    if not callable(validator):
        return 0
    invalid = 0
    for tr in final_tl.get("tracks", []) or []:
        if not str(tr.get("kind", "")).endswith("video"):
            continue
        for c in tr.get("clips", []) or []:
            try:
                from clipwright.schema.timeline import Clip as _Clip

                if not validator(_Clip.model_validate(c)):
                    invalid += 1
                    meta = c.setdefault("metadata", {}) or {}
                    meta["type_violation"] = True
            except Exception:  # noqa: BLE001 — 单条校验异常不影响其余
                continue
    return invalid


def finalize(
    final_tl: Optional[dict],
    *,
    plugin: Any = None,
    extra_params: Optional[dict[str, Any]] = None,
    pid: str = "",
    on_degradation: Optional[Callable[[str, str, str, str], None]] = None,
) -> dict:
    """最终时间线收尾：类型 transform → 规范校验 → 片尾授权名单。

    `on_degradation(kind, detail, scope, impact)`：降级要**可见**（旧实现写进
    AgentContext.extra_params，现在由调用方决定往哪记）。

    任何一步失败都只降级、不抛：时间线是渲染真值，不能因为"片尾加不上"就整片不出。
    """
    if not final_tl:
        return final_tl or {}
    params = dict(extra_params or {})
    problem = on_degradation or (lambda *a, **k: None)

    if plugin is not None:
        # 类型插件 transform（分辨率/帧率/字幕/时长截断）
        try:
            from clipwright.schema.timeline import Timeline as _Tl

            processed = plugin.post_process_timeline(_Tl.model_validate(final_tl))
            final_tl = processed.model_dump(mode="json")
        except Exception as e:  # noqa: BLE001
            logger.warning("类型插件 transform 失败（保留原时间线）: %s", e)
            problem("plugin_transform_failed",
                    f"类型插件 {getattr(plugin, 'plugin_id', '?')} 处理失败：{e}",
                    "global", "分辨率/帧率/字幕/时长截断等类型规范未生效")
        try:
            invalid = _validate_clips(final_tl, plugin)
            if invalid:
                logger.info("%s 类型规范校验：%d 个片段不合规（已打标）", pid, invalid)
                problem("type_violation",
                        f"{invalid} 个片段不符合 {getattr(plugin, 'plugin_id', '?')} 规范",
                        "clips", "违规片段已打标，成片可能不满足类型约束")
        except Exception as e:  # noqa: BLE001
            logger.warning("类型 validate_clip 校验失败（跳过）: %s", e)

    # 片尾授权名单（默认开；`credit_outro=false` 关闭）
    if params.get("credit_outro", True):
        try:
            credits = collect_credit_lines(final_tl)
            if credits[0]:
                final_tl = append_credit_outro(final_tl, credits)
                logger.info("%s 已追加素材授权片尾（%d 条来源）", pid, credits[1])
        except Exception:  # noqa: BLE001 — 片尾失败不影响成片
            logger.exception("追加片尾授权失败（已忽略）")
    return final_tl


__all__ = ["CREDIT_DUR_SEC", "CREDIT_MAX_SOURCES", "append_credit_outro",
           "collect_credit_lines", "finalize"]
