"""Plugin 数据模型 — 类型插件与第三方插件接口定义。

本模块定义插件系统的核心类型，包括：
1. 视频类型插件（Category Plugin）— 内置的插件，对应不同视频风格的剪辑策略
2. 第三方插件（Third-party Plugin）— 外部扩展，可新增素材源、Agent 策略、能力工具
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class PluginKind(str, Enum):
    """插件类型枚举。"""
    CATEGORY = "category"          # 视频类型插件（内置）
    MATERIAL_SOURCE = "material"   # 素材库/素材源插件
    AGENT_STRATEGY = "agent"       # Agent 策略插件
    CAPABILITY = "capability"      # 能力/工具插件
    STYLE = "style"                # 风格解释器插件
    EDITOR = "editor"              # 编辑器插件（前端）


class ContributionDecl(BaseModel):
    """A-3：manifest 预声明的贡献面（「声明即授权」的最小单元）。

    运行时注册未在此声明的 (kind, id) 将被门面拒绝——这是进程内 Python
    插件唯一可执行的权限模型（无沙箱前提下的 Gate G-2 收口）。
    """
    kind: str = Field(description="贡献类型（见 ContributionKind 的 14 类）")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$", description="贡献 id（同 kind 内唯一）")
    meta: dict[str, Any] = Field(default_factory=dict, description="kind 专属元数据")


class PluginManifest(BaseModel):
    """插件元信息。"""
    id: str = Field(description="插件唯一 ID")
    name: str
    version: str = Field(default="1.0.0")
    kind: PluginKind
    description: str = Field(default="")
    author: str = Field(default="")
    entry_point: str = Field(default="", description="插件入口模块路径")

    # ── A-3: 统一贡献契约声明（「声明即授权」）──
    api_version: str = Field(
        default="1",
        description='贡献契约版本；"2" 起支持 contributions 声明与门面注册',
    )
    contributions: list[ContributionDecl] = Field(
        default_factory=list,
        description="预声明的贡献 id 清单——运行时注册未在此声明的 id 将被拒绝",
    )
    override_builtin: bool = Field(
        default=False,
        description="允许覆盖内置贡献（需签名 + 管理员权限，由加载器入口校验）",
    )

    @field_validator("entry_point")
    @classmethod
    def _validate_entry_point(cls, v: str) -> str:
        """P1-14: entry_point 只允许合法 Python 模块路径（防 path shadowing/
        相对路径注入——旧实现无任何 pattern，插件可声明 'requests' 之类
        顶层模块名 shadow 标准库）。"""
        if v and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", v):
            raise ValueError(
                f"entry_point 必须是合法的模块路径（如 main 或 pkg.main），收到: {v!r}"
            )
        return v

    # ── M10: manifest 增强（市场开放前最低治理字段）──
    license: str = Field(default="", description="开源协议（如 MIT/Apache-2.0）")
    homepage: str = Field(default="", description="插件主页/仓库地址")
    compat_api_version: str = Field(default="", description="声明的兼容 API 版本（如 0.1.0）")
    permissions: list[str] = Field(default_factory=list, description="声明的权限集合（如 network/fs_write/shell）")
    dependencies: list[str] = Field(default_factory=list, description="依赖的其他插件 ID（M2 依赖解析）")
    signature: str = Field(default="", description="manifest 签名（HMAC-SHA256 hex，M1）")
    config_schema_version: int = Field(default=1, description="配置结构版本（M15 配置迁移）")

    # ── P9: 运行形态（插件子进程沙箱）──
    runtime: str = Field(
        default="inprocess",
        description="inprocess=宿主进程内（内置/受信任插件）；subprocess=独立子进程（宿主不 import 其代码）",
    )
    runtime_config: dict[str, Any] = Field(
        default_factory=dict,
        description="运行形态配置：call_timeout（单次调用超时秒数）等",
    )

    @field_validator("runtime")
    @classmethod
    def _validate_runtime(cls, v: str) -> str:
        value = str(v or "inprocess").strip().lower()
        if value not in ("inprocess", "subprocess"):
            raise ValueError(f"runtime 只能是 inprocess / subprocess，收到: {v!r}")
        return value

    model_config = {"use_enum_values": True}


class PluginMetadata(BaseModel):
    """插件的运行时元信息。"""
    manifest: PluginManifest
    enabled: bool = Field(default=True)
    has_ui: bool = Field(default=False, description="是否定义了前端 UI (ui.json)")
    config: dict[str, Any] = Field(default_factory=dict)
    # 签名/权限状态
    signed: bool = Field(default=False, description="manifest 是否携带签名")
    verified: bool = Field(default=False, description="签名是否已验证通过（配置签名密钥时有效）")
    # 依赖解析结果
    dependency_ok: bool = Field(default=True, description="依赖是否全部满足")
    missing_dependencies: list[str] = Field(default_factory=list)
    # SA-2: 插件注册的自定义 Agent 名（AgentRegistry 归属，管理面展示）
    agents: list[str] = Field(default_factory=list)
    # P12: 已注册贡献（门面账本快照，形态 [{kind, id}]）与「声明了但未注册」的键
    contributions: list[dict[str, Any]] = Field(
        default_factory=list, description="已注册贡献（kind/id）",
    )
    declared_only: list[str] = Field(
        default_factory=list, description="manifest 声明但运行时未注册的贡献键（kind:id）",
    )


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
