"""素材库数据模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MaterialType(str, Enum):
    """素材类型。"""
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"


class MaterialAsset(BaseModel):
    """统一的素材条目模型。"""
    id: str = Field(description="素材唯一 ID")
    title: str = Field(default="", description="素材标题/描述")
    type: MaterialType = Field(default=MaterialType.VIDEO)

    # 访问方式（至少一个有值）
    url: Optional[str] = Field(default=None, description="远程 URL")
    local_path: Optional[str] = Field(default=None, description="本地路径")

    # 预览
    thumbnail_url: Optional[str] = Field(default=None, description="缩略图 URL")

    # 元信息
    tags: list[str] = Field(default_factory=list, description="标签")
    duration_sec: Optional[float] = Field(default=None, description="时长（秒）")
    file_size_bytes: Optional[int] = Field(default=None)
    resolution: Optional[str] = Field(default=None, description="如 1920x1080")
    source: str = Field(default="", description="来源源 ID")
    # P5-B6: 版权/许可信息（素材源可提供；缺省为空，前端可展示）
    license: str = Field(default="", description="许可/版权标注")

    # 扩展
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = {"use_enum_values": True}


class MaterialSearchResult(BaseModel):
    """素材搜索的一条结果。"""
    asset: MaterialAsset
    score: float = Field(default=0.0, ge=0, le=1, description="相关度 0-1")
    matched_keywords: list[str] = Field(default_factory=list)
    source_name: str = Field(default="", description="来源名称")
    scene_index: Optional[int] = Field(default=None, description="匹配的场景序号")


class MaterialSourceConfig(BaseModel):
    """素材源的配置定义。"""
    id: str = Field(description="源 ID")
    name: str = Field(default="")
    kind: str = Field(description="json_catalog / url / rag / local")
    enabled: bool = Field(default=True)
    config: dict[str, Any] = Field(default_factory=dict)


class MaterialLibraryConfig(BaseModel):
    """素材库的完整配置（对应 JSON 文件）。"""
    name: str = Field(default="default")
    sources: list[MaterialSourceConfig] = Field(default_factory=list)
