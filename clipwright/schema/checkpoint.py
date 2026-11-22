"""阶段检查点：断点续跑的持久化契约。

只保存“可恢复的单位进度 + 已完成产物”，不保存 prompt 或大段对话。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageCheckpoint(BaseModel):
    """一个阶段在某个输入摘要下的可恢复进度。"""

    schema_version: int = 1
    stage: str
    task_id: str = ""
    input_digest: str
    attempt: int = 1
    completed_units: list[int] = Field(default_factory=list)
    partial_outputs: list[dict] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list)
    updated_at: str = ""


__all__ = ["StageCheckpoint"]
