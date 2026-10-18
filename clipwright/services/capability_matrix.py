"""能力矩阵（路线 C / C0 交付物）。

解决的问题：**"agent 能调哪些能力"不能靠嘴说**。现状 49 个已注册工具里只有个别声明了
`agent_callable`，其余都是管线内部程序化调用；如果直接写 agent 循环，agent 会发现
自己手上只有几把扳手。

本模块以**实际注册表**为事实来源，为每个能力标注：

- `kind`：tool / skill / pipeline / subagent（后两者是编排层能力，不是 ToolRegistry 条目）
- `tier`：权限档 read / write_local / costly / dangerous（PRD F2 四级）
- `agent_callable`：当前是否已声明可被 LLM tool-use 调用（**C0 的现实**，不是目标）
- `available`：依赖是否满足（缺 ffmpeg 等）
- `idempotent`：同入参重复执行是否安全（read 档按定义可重放）
- `dry_run`：是否支持"只算不做"（C0 现状为 0；C1 要补齐）
- `notes`：为什么这么定档

两条纪律：

1. **覆盖度强校验**：注册表里出现的每个工具必须在本模块的 `TIER_MAP` 里有明确档位，
   新增工具不填 → `build_matrix()` 直接抛错、测试红灯（防止"悄悄多出一个没人管的能力"）。
2. **不凭文档填**：矩阵由代码生成（`python -m clipwright.services.capability_matrix`），
   人只在 `TIER_MAP` 里裁档位。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Optional

TIERS: tuple[str, ...] = ("read", "write_local", "costly", "dangerous")

PermissionTier = Literal["read", "write_local", "costly", "dangerous"]
CapabilityKind = Literal["tool", "skill", "pipeline", "subagent", "meta"]


class CapabilityMatrixError(RuntimeError):
    """矩阵构建失败（通常是新增能力未裁档位）。"""


# ── 权限档裁定表（以实际注册表逐条核对；新增工具必须在此登记）───────────────
#
# 档位判定口径（写死在注释里，避免下次有人凭感觉改）：
#   read        不写任何文件、不改状态，只读/分析 → 可重放、agent 自主调用
#   write_local 只写项目工作区产物（可重做、可丢弃）→ agent 自主，但要幂等键
#   costly      花钱或长耗时（生成类、TTS、付费检索、转写）→ 受预算闸
#   dangerous   可执行命令、可动系统、可把本地内容发到外部 → 必须人工授权
TIER_MAP: dict[str, PermissionTier] = {
    # —— 视频加工（写工作区产物）——
    "video_trim": "write_local",
    "video_concat": "write_local",
    "video_overlay": "write_local",
    "video_crop": "write_local",
    "video_thumbnail": "write_local",
    "video_speed": "write_local",
    "video_blur": "write_local",
    "video_stabilize": "write_local",
    "background_remove": "write_local",
    "effect_vignette": "write_local",
    "watermark": "write_local",
    "chroma_key": "write_local",
    "speed_ramp": "write_local",
    "color_correct": "write_local",
    "color_match": "write_local",
    "lut_apply": "write_local",
    "transition_apply": "write_local",
    "subtitle_burn": "write_local",
    "typewriter_animation": "write_local",
    "tracking_text": "write_local",
    "text_diagram": "write_local",
    # —— 音频加工（写工作区产物）——
    "audio_extract": "write_local",
    "audio_normalize": "write_local",
    "audio_mix": "write_local",
    "audio_replace": "write_local",
    # —— 只读分析（可重放）——
    "media_probe": "read",
    "bpm_detect": "read",
    "scene_detect": "read",
    "frame_validator": "read",
    "black_frame_detect": "read",
    "audio_silence_detect": "read",
    "subtitle_overflow": "read",
    "semantic_match": "read",
    "material_filter": "read",
    "timeline_view": "read",
    "content_query": "read",
    "project_memory": "read",
    "list_animations": "read",
    "vision_llm": "costly",        # 走视觉模型，按次计费
    "face_detect": "read",
    # —— 花钱 / 长耗时 ——
    "generate_text_video": "costly",
    "whisper_transcribe": "costly",
    "text_to_speech": "costly",
    "voice_clone": "costly",
    "event_spot": "costly",        # 热点检索：外部搜索
    "web_search": "costly",
    "web_fetch": "costly",         # 外网读取：不计费但可能触碰外网配额
    "video_download": "dangerous",  # 下载外部资源到本地：来源不可控
    "describe_llm_mg": "costly",
    # —— 文件读写（主 Agent / 子代理基础能力）——
    "read_file": "read",
    "list_dir": "read",
    "write_file": "write_local",   # 只写白名单目录，父目录不自动创建
}

#: 编排层能力（不属于 ToolRegistry，但 agent 同样可以"调用"）
META_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "name": "run_pipeline",
        "kind": "pipeline",
        "tier": "costly",
        "idempotent": False,
        "dry_run": True,   # 管线自带 dry_run：只生成粗剪时间线预览
        "notes": "老 6 阶段管线，降级为可调用能力；常规套路/预算敏感时应优先选它",
    },
    {
        "name": "spawn_subagent",
        "kind": "subagent",
        "tier": "costly",
        "idempotent": False,
        "dry_run": False,
        "notes": "星型拓扑、深度 1；只读角色不需审核门，带执行权限需两级审核",
    },
    {
        "name": "run_shell",
        "kind": "meta",
        "tier": "dangerous",
        "idempotent": False,
        "dry_run": False,
        "notes": "经 ExecutionGateway：硬底线标记 + 独立 LLM 判定 + 两级确认",
    },
    {
        "name": "run_python",
        "kind": "meta",
        "tier": "dangerous",
        "idempotent": False,
        "dry_run": False,
        "notes": "同上；脚本落临时文件、默认禁网",
    },
)

#: 已支持 dry-run 的能力（C0 现状：只有管线；C1 要为 write_local/costly 补齐）
DRY_RUN_CAPABLE: frozenset[str] = frozenset({"run_pipeline"})

#: 只读能力按定义可重放；写入/执行类默认不可重放（重放靠幂等键复用结果）
_READ_TIER: PermissionTier = "read"


@dataclass(frozen=True)
class CapabilityEntry:
    name: str
    kind: CapabilityKind
    tier: PermissionTier
    available: bool
    agent_callable: bool
    idempotent: bool
    dry_run: bool
    notes: str = ""

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _tool_entries() -> list[CapabilityEntry]:
    from clipwright.tool import register_builtin_tools
    from clipwright.tool.registry import ToolRegistry

    # 只在空注册表时初始化：重复注册会触发"重复注册"告警，把真正的告警淹掉
    if not ToolRegistry.list():
        register_builtin_tools()

    out: list[CapabilityEntry] = []
    unknown: list[str] = []
    for info in ToolRegistry.list():
        name = str(info.name)
        tier = TIER_MAP.get(name)
        if tier is None:
            unknown.append(name)
            continue
        tool = ToolRegistry.get(name)
        out.append(
            CapabilityEntry(
                name=name,
                kind="tool",
                tier=tier,
                available=bool(info.available),
                agent_callable=bool(getattr(tool, "agent_callable", False)),
                idempotent=tier == _READ_TIER,
                dry_run=name in DRY_RUN_CAPABLE,
                notes="",
            )
        )
    if unknown:
        raise CapabilityMatrixError(
            "以下工具未在 capability_matrix.TIER_MAP 里裁权限档："
            f"{sorted(unknown)}（新增能力必须显式定档，不能静默漏掉）"
        )
    stale = sorted(set(TIER_MAP) - {str(i.name) for i in ToolRegistry.list()})
    if stale:
        raise CapabilityMatrixError(
            f"TIER_MAP 里有已不存在的工具：{stale}（删工具时同步删档位）"
        )
    return out


def _skill_entries() -> list[CapabilityEntry]:
    from clipwright.skill import register_builtin_skills
    from clipwright.skill.registry import SkillRegistry

    if not SkillRegistry.list():
        register_builtin_skills()

    out: list[CapabilityEntry] = []
    for info in SkillRegistry.list():
        out.append(
            CapabilityEntry(
                name=str(info.name),
                kind="skill",
                # Skill 目前都是"分析/生成字幕"这类只写工作区产物的组合能力
                tier="write_local",
                available=bool(info.available),
                agent_callable=True,   # Skill 走 to_llm_tool，本身就是给 LLM 用的
                idempotent=False,
                dry_run=str(info.name) in DRY_RUN_CAPABLE,
                notes="组合能力（4 个内置 Skill）",
            )
        )
    return out


def _meta_entries() -> list[CapabilityEntry]:
    return [
        CapabilityEntry(
            name=str(m["name"]),
            kind=str(m["kind"]),  # type: ignore[arg-type]
            tier=str(m["tier"]),  # type: ignore[arg-type]
            available=True,
            agent_callable=True,
            idempotent=bool(m["idempotent"]),
            dry_run=bool(m["dry_run"]) or str(m["name"]) in DRY_RUN_CAPABLE,
            notes=str(m.get("notes", "")),
        )
        for m in META_CAPABILITIES
    ]


def build_matrix() -> list[CapabilityEntry]:
    """构建完整能力矩阵（工具 + Skill + 编排层能力），按 kind/tier/name 排序。"""
    matrix = _tool_entries() + _skill_entries() + _meta_entries()
    order = {k: i for i, k in enumerate(("tool", "skill", "pipeline", "subagent", "meta"))}
    return sorted(matrix, key=lambda e: (order.get(e.kind, 9), e.tier, e.name))


def coverage(matrix: Optional[Iterable[CapabilityEntry]] = None) -> dict[str, Any]:
    """覆盖度统计（C1 的验收口径 A7 就用这个数）。"""
    entries = list(matrix if matrix is not None else build_matrix())
    by_tier: dict[str, int] = {t: 0 for t in TIERS}
    by_kind: dict[str, int] = {}
    for e in entries:
        by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    total = len(entries)
    callable_n = sum(1 for e in entries if e.agent_callable)
    return {
        "total": total,
        "by_tier": by_tier,
        "by_kind": by_kind,
        "agent_callable": callable_n,
        "agent_callable_ratio": round(callable_n / total, 4) if total else 0.0,
        "available": sum(1 for e in entries if e.available),
        "dry_run_capable": sum(1 for e in entries if e.dry_run),
        "dangerous": [e.name for e in entries if e.tier == "dangerous"],
    }


def render_markdown(matrix: Optional[Iterable[CapabilityEntry]] = None) -> str:
    """渲染成文档表格（写进 docs/capability-matrix.md）。"""
    entries = list(matrix if matrix is not None else build_matrix())
    stat = coverage(entries)
    lines = [
        "# 能力矩阵（自动生成，勿手改）",
        "",
        "> 生成命令：`python -m clipwright.services.capability_matrix`",
        "> 事实来源：`ToolRegistry` / `SkillRegistry` 实际注册表 + `capability_matrix.META_CAPABILITIES`。",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 能力总数 | {stat['total']} |",
        f"| 按类型 | {json.dumps(stat['by_kind'], ensure_ascii=False)} |",
        f"| 按权限档 | {json.dumps(stat['by_tier'], ensure_ascii=False)} |",
        f"| LLM 可调用（当前） | {stat['agent_callable']}（{stat['agent_callable_ratio']:.0%}） |",
        f"| 依赖可用 | {stat['available']} |",
        f"| 支持 dry-run（当前） | {stat['dry_run_capable']} |",
        f"| dangerous 档 | {', '.join(stat['dangerous']) or '（无）'} |",
        "",
        "## 明细",
        "",
        "| 能力 | 类型 | 权限档 | 可用 | LLM 可见 | 幂等 | dry-run | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            "| `{name}` | {kind} | {tier} | {avail} | {callable} | {idem} | {dry} | {notes} |".format(
                name=e.name,
                kind=e.kind,
                tier=e.tier,
                avail="✓" if e.available else "✗",
                callable="✓" if e.agent_callable else "✗",
                idem="✓" if e.idempotent else "✗",
                dry="✓" if e.dry_run else "✗",
                notes=e.notes,
            )
        )
    lines.append("")
    lines.append(
        "> 覆盖度是 C1 的硬门槛（PRD A7）：`LLM 可见` 这一列是**旧的** `agent_callable` 标记，"
        "不代表 Kernel 实际能看到什么——真正的工具面见下一节。"
    )

    # ── Kernel 工具面（C1）───────────────────────────────
    try:
        from clipwright.tool.surface import SURFACE_TIERS, surface_stats

        sstat = surface_stats()
        lines += [
            "",
            "## Kernel 工具面（C1）",
            "",
            "> 口径：`read` / `write_local` / `costly` 可被 Kernel 直调；"
            "`dangerous` 只能经执行网关（PRD F6 红线 2/5）。",
            "",
            "| 指标 | 值 |",
            "|---|---|",
            f"| 直调档位 | {', '.join(SURFACE_TIERS)} |",
            f"| 工具面条目 | {sstat['surface_count']}（可用 {sstat['surface_available']}） |",
            f"| 按档位 | {json.dumps(sstat['by_tier'], ensure_ascii=False)} |",
            f"| 覆盖率（可用安全工具） | {sstat['coverage_ratio']:.0%}（池 {sstat['pool']} 项） |",
            f"| 真实 dry-run | {sstat['dry_run_capable']} 项（其余走结构预检） |",
            f"| 结构预检 | {sstat['preflight_capable']} 项（参数/路径可验证，不验滤镜语义） |",
            f"| 排除的 dangerous | {', '.join(sstat['dangerous_excluded']) or '（无）'} |",
            "",
        ]
    except Exception as e:  # noqa: BLE001 — 工具面不可用时退化为纯矩阵
        lines += ["", f"> Kernel 工具面统计不可用：{e}", ""]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_path = "docs/capability-matrix.md"
    if "--out" in args:
        out_path = args[args.index("--out") + 1]
    md = render_markdown()
    from pathlib import Path

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")
    stat = coverage()
    print(
        f"能力矩阵已写入 {target}：共 {stat['total']} 项，"
        f"LLM 可见 {stat['agent_callable']} 项（{stat['agent_callable_ratio']:.0%}），"
        f"dry-run {stat['dry_run_capable']} 项"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
