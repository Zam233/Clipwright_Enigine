"""任务收尾把成片时间线写回「发起它的项目」（E2E 2026-10-29）。

背景：`POST /api/requirements/proceed` 请求体里带着 `project_id`，字段注释写着
"管线完成后把 final_timeline 保存到该项目（防止内存结果 60s 清理后丢失）"，但
**Task 路径（主 Agent）从未实现这一跳**：

- 任务跑完，黑板里有 `final_timeline`（实测 art_46e831df：6 轨 / 503 片段 / 702.3s）；
- 项目 `proj_xxx` 的时间线仍是 0 轨（`updated_at` 停在 proceed 之前）；
- 前端导出页按钮因 `timeline.tracks.length === 0` 恒禁用 → 用户拿到"成片时间线已落地"
  的汇报，却没有任何地方能打开或渲染它。

`POST /api/task/{task_id}/to-project` 只能**新建**一个项目，救不了"从已有项目发起"的
场景（用户会得到两个项目：一个空的原项目 + 一个新建的）。所以这里补上回写。

约定：
- 只写 `timeline` 一个键（`ProjectManager.save` 是合并语义，其余字段不动）；
- 时间线可能是黑板里的 dict，也可能是落盘指针 `{path, sha256, bytes}` → 统一解析；
- 任何失败只记日志，绝不把已经成功的任务拖成失败。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger


def _signature(timeline: Any) -> str:
    """时间线内容指纹（只算 tracks，键序稳定）。

    为什么要指纹而不是"来源标记"：中间回写必须区分两种情况——
    ① 项目里这份时间线**就是我们写的**（可以继续覆盖，越写越完整）；
    ② 用户在编辑器里**自己改过**（绝不能覆盖）。
    旧实现用 `metadata.source == f"task:{id}"` 判断，但**从来没有人写过这个标记**，
    于是项目一旦有轨道（第一次回写之后）就永远跳过 → 时间轴只在第一次更新，
    之后直到任务结束都不再动（"实时同步"实际是坏的）。
    指纹写在 `metadata.agent_sig`，任何任务都能比对：与当前内容一致 = 没人动过。
    """
    tracks = (timeline or {}).get("tracks") if isinstance(timeline, dict) else None
    try:
        body = json.dumps(tracks or [], ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except Exception:  # noqa: BLE001 — 序列化失败就当"不可比较"
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def resolve_timeline(value: Any) -> Optional[dict[str, Any]]:
    """把黑板里的时间线值解析成真时间线 dict（兼容落盘指针）。"""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("tracks"), list):
        return value
    path = value.get("path")
    if isinstance(path, str) and path:
        try:
            p = Path(path)
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("tracks"), list):
                    return data
        except Exception as exc:  # noqa: BLE001 — 指针坏了就当没有时间线
            logger.warning("时间线指针读取失败 %s: %s", path, exc)
    return None


def project_id_of(task: Any) -> str:
    """任务发起时归属的项目 id（proceed 写入 `proceed_project_id`）。"""
    extra = dict(getattr(getattr(task, "request", None), "extra", {}) or {})
    for key in ("proceed_project_id", "project_id"):
        pid = str(extra.get(key) or "").strip()
        if pid:
            return pid
    return ""


def _timeline_from_board(task_id: str) -> Optional[dict[str, Any]]:
    try:
        from clipwright.services import task_blackboard as bb

        board = bb.get(task_id) or {}
    except Exception as exc:  # noqa: BLE001 — 黑板不可用不阻断
        logger.warning("读黑板失败 %s: %s", task_id, exc)
        return None
    return resolve_timeline(board.get("final_timeline") or board.get("timeline"))


def _timeline_from_results(task_id: str) -> Optional[dict[str, Any]]:
    try:
        from clipwright.services.task_results import last_result

        result = last_result(task_id) or {}
    except Exception:  # noqa: BLE001
        return None
    return resolve_timeline(result.get("timeline"))


def sync_timeline_to_project(task_id: str, task: Any, *, incremental: bool = False) -> str:
    """回写成片时间线。返回写入的项目 id（没写则空串）。

    `incremental=True`（2026-10-31，时间轴实时同步）用于**任务进行中**的中间回写：
    每派完一个阶段就把黑板里的当前时间线写进项目，编辑器那边轮询到就会即时刷新——
    旧实现只在任务收尾写一次，用户在跑片子的半小时里看到的始终是空时间线。
    """
    pid = project_id_of(task)
    if not pid:
        return ""
    timeline = _timeline_from_board(task_id) or _timeline_from_results(task_id)
    if timeline is None:
        logger.info("任务 %s 没有可回写的时间线（项目 %s 保持原样）", task_id, pid)
        return ""
    try:
        from clipwright.api.project import _manager

        # 中间回写不能覆盖"用户已保存的编辑"：判据是内容指纹——
        # 项目里这份时间线的 `metadata.agent_sig` 与它自己的 tracks 一致，
        # 说明"最后动它的是 agent"，可以继续覆盖；不一致说明用户改过，跳过。
        if incremental:
            try:
                existing = _manager.load(pid) or {}
                cur = existing.get("timeline") or {}
                if (cur.get("tracks") or []):
                    prev_sig = str((cur.get("metadata") or {}).get("agent_sig") or "")
                    if prev_sig != _signature(cur):
                        logger.info("跳过中间回写：项目 %s 的时间线被用户改过（指纹不一致）", pid)
                        return ""
            except FileNotFoundError:
                pass
        # 打标：来源 + 内容指纹，供下一次回写与"是否被用户改过"判断
        timeline = dict(timeline)
        meta = dict(timeline.get("metadata") or {})
        meta["source"] = f"task:{task_id}"
        meta["agent_sig"] = _signature(timeline)
        timeline["metadata"] = meta
        _manager.save(pid, {"timeline": timeline})
        # `save` 会走一次 schema 归一化（补默认值），指纹要以**落盘后的内容**为准，
        # 否则下一次比对必然不一致 → 又变成"只写一次"。
        try:
            saved = (_manager.load(pid) or {}).get("timeline") or {}
            sig = _signature(saved)
            if sig and str((saved.get("metadata") or {}).get("agent_sig") or "") != sig:
                saved_meta = dict(saved.get("metadata") or {})
                saved_meta["agent_sig"] = sig
                saved_meta["source"] = f"task:{task_id}"
                saved["metadata"] = saved_meta
                _manager.save(pid, {"timeline": saved})
        except Exception as exc:  # noqa: BLE001 — 补指纹失败只影响下一次能否覆盖
            logger.warning("回写时间线后补指纹失败 %s: %s", pid, exc)
    except FileNotFoundError:
        logger.warning("回写时间线失败：项目不存在 %s（task=%s）", pid, task_id)
        return ""
    except Exception as exc:  # noqa: BLE001 — 回写失败不影响任务结论
        logger.warning("回写时间线失败 %s ← %s: %s", pid, task_id, exc)
        return ""
    tracks = len(timeline.get("tracks") or [])
    clips = sum(len(t.get("clips") or []) for t in (timeline.get("tracks") or []))
    logger.info("任务 %s 的时间线已写回项目 %s（%d 轨 / %d 片段%s）",
                task_id, pid, tracks, clips, "，增量" if incremental else "")
    try:
        from clipwright.services.task_events import emit

        # 用**已有的** `artifact` 事件，不新增事件类型（契约仍是 29 种）；
        # 前端据 kind=timeline 触发时间线刷新。旧实现发的是不存在的 `project_sync`，
        # emit 直接抛错并被吞掉 —— 事件从来没出现过（所以"实时同步"根本收不到信号）。
        emit(task_id, "artifact",
             f"时间线已写回项目 {pid}（{tracks} 轨 / {clips} 片段）",
             {"kind": "timeline", "project_id": pid, "tracks": tracks, "clips": clips,
              "incremental": incremental},
             span="kernel")
    except Exception:  # noqa: BLE001 — 事件通道不可用不影响结果
        pass
    return pid


__all__ = ["project_id_of", "resolve_timeline", "sync_timeline_to_project"]
