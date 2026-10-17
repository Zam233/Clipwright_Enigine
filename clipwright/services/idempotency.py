"""P3-13: 跨端点共享幂等存储。

`/api/pipeline/run-async` 与 `/api/requirements/proceed` 曾各持一套幂等表——
同一 Idempotency-Key 在两条入口路径上互不识别，客户端重试会起两条管线。
两处统一读写本模块的键空间（key → pipeline_id）。
"""

from __future__ import annotations

from typing import Optional

#: Idempotency-Key（或 proceed 的 session_id）→ pipeline_id
_idempotency_pipeline: dict[str, str] = {}

#: 键空间上限（超出时淘汰最旧的 1/10，保持行为与原实现一致）
_MAX_KEYS = 500


def lookup(key: str) -> Optional[str]:
    """按键查询既有管线 id（无则 None）。"""
    return _idempotency_pipeline.get(key) if key else None


def bind(key: str, pipeline_id: str) -> None:
    """写入映射并做容量治理。"""
    if not key:
        return
    _idempotency_pipeline[key] = pipeline_id
    if len(_idempotency_pipeline) > _MAX_KEYS:
        for _k in list(_idempotency_pipeline)[: max(1, len(_idempotency_pipeline) - _MAX_KEYS)]:
            _idempotency_pipeline.pop(_k, None)


def bind_if_absent(key: str, pipeline_id: str) -> Optional[str]:
    """原子占位：键已存在返回既有 id，否则写入并返回 None。

    检查与写入之间无 await，无并发窗口（供 set-before-run 占位使用）。
    """
    if not key:
        return None
    existing = _idempotency_pipeline.get(key)
    if existing:
        return existing
    _idempotency_pipeline[key] = pipeline_id
    return None


def drop(key: str, pipeline_id: str) -> None:
    """仅当键仍指向该管线时删除（回滚失败的占位）。"""
    if key and _idempotency_pipeline.get(key) == pipeline_id:
        _idempotency_pipeline.pop(key, None)


def drop_by_pipeline(pipeline_id: str) -> None:
    """清理指向指定管线的全部键（管线终态清理）。"""
    for k, v in list(_idempotency_pipeline.items()):
        if v == pipeline_id:
            _idempotency_pipeline.pop(k, None)


def clear_all() -> None:
    """清空（测试/重置用）。"""
    _idempotency_pipeline.clear()
