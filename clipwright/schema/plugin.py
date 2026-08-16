"""Plugin 数据模型 — 类型插件与第三方插件接口定义。

本模块定义插件系统的核心类型，包括：
1. 视频类型插件（Category Plugin）— 内置的插件，对应不同视频风格的剪辑策略
2. 第三方插件（Third-party Plugin）— 外部扩展，可新增素材源、Agent 策略、能力工具
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PluginKind(str, Enum):
    """插件类型枚举。"""
    CATEGORY = "category"          # 视频类型插件（内置）
    MATERIAL_SOURCE = "material"   # 素材库/素材源插件
    AGENT_STRATEGY = "agent"       # Agent 策略插件
    CAPABILITY = "capability"      # 能力/工具插件
    STYLE = "style"                # 风格解释器插件
    EDITOR = "editor"              # 编辑器插件（前端）


class PluginManifest(BaseModel):
    """插件元信息。"""
    id: str = Field(description="插件唯一 ID")
    name: str
    version: str = Field(default="1.0.0")
    kind: PluginKind
    description: str = Field(default="")
    author: str = Field(default="")
    entry_point: str = Field(default="", description="插件入口模块路径")

    # ── M10: manifest 增强（市场开放前最低治理字段）──
    license: str = Field(default="", description="开源协议（如 MIT/Apache-2.0）")
    homepage: str = Field(default="", description="插件主页/仓库地址")
    compat_api_version: str = Field(default="", description="声明的兼容 API 版本（如 0.1.0）")
    permissions: list[str] = Field(default_factory=list, description="声明的权限集合（如 network/fs_write/shell）")
    dependencies: list[str] = Field(default_factory=list, description="依赖的其他插件 ID（M2 依赖解析）")
    signature: str = Field(default="", description="manifest 签名（HMAC-SHA256 hex，M1）")
    config_schema_version: int = Field(default=1, description="配置结构版本（M15 配置迁移）")

    model_config = {"use_enum_values": True}


class PluginMetadata(BaseModel):
    """插件的运行时元信息。"""
    manifest: PluginManifest
    enabled: bool = Field(default=True)
    has_ui: bool = Field(default=False, description="是否定义了前端 UI (ui.json)")
    config: dict[str, Any] = Field(default_factory=dict)
    # M1: 签名/权限状态
    signed: bool = Field(default=False, description="manifest 是否携带签名")
    verified: bool = Field(default=False, description="签名是否已验证通过（配置签名密钥时有效）")
    # M2: 依赖解析结果
    dependency_ok: bool = Field(default=True, description="依赖是否全部满足")
    missing_dependencies: list[str] = Field(default_factory=list)


class CategoryPluginSpec(BaseModel):
    """视频类型插件的规格定义。"""
    plugin_id: str
    display_name: str
    description: str = Field(default="")

    # 剪辑特征
    avg_shot_range_sec: tuple[float, float] = Field(default=(3.0, 10.0))
    transition_affinity: list[str] = Field(default_factory=list)
    animation_density: str = Field(default="medium")

    # 支持的 Persona 维度
    supported_tones: list[str] = Field(default_factory=list)

    # 标注模板（用于示例层自动分类）
    annotation_templates: list[str] = Field(default_factory=list)
