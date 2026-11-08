"""GenUI 展示 spec：数据契约、清洗器与两个确定性构造器。

**它是什么**：一份**只用于展示**的 JSON（`version=1` + 组件 `items` 树），让创意简报、
规划书、右侧副驾驶里能出现表格/指标/步骤这类结构化视图。参考实现是 DSH 的 `dsh-genui`
（`dsh-ui` 围栏 + 组件白名单）；本仓刻意只取一个**安全子集**：
不含 HTML/脚本/CSS、不含远端图表引擎、不含 3D/Mermaid，动作也只允许宿主已实现的三种。

**三条纪律**

1. **权威数据不在 spec 里**：`CreativeBrief` / `ProductionPlan` 的字段与规划书 markdown
   才是用户确认与下游执行的对象；spec 丢了、坏了、被禁用了，业务照常。
2. **不信任模型输出**：模型给的 spec 一律过 `sanitize_spec()`——未知组件降级为占位文本、
   危险键剔除、超限截断、非法 action 丢弃，**永不** eval / 拼 HTML / 反射取属性。
3. **可缺省**：模型不给 spec 时，宿主用 `build_brief_genui()` / `build_plan_genui()`
   从权威字段**确定性地**生成一份（不调模型、不猜）。

组件白名单见 ``GENUI_COMPONENT_TYPES``；上限见 ``MAX_*``。前端
`src/features/genui/validate.ts` 有同一套规则的第二道闸（防旧缓存/被改过的响应）。
"""
from __future__ import annotations

import json
from typing import Any

#: 契约版本。**加字段不改版本**（前端容忍未知字段）；**换结构才升版本**。
GENUI_VERSION = 1

#: 组件白名单（13 类，全部可纯 React 渲染、零外部依赖、零模型可控网络/脚本）。
GENUI_COMPONENT_TYPES = frozenset({
    "text", "list", "callout", "badge", "stat", "progress", "table",
    "steps", "divider", "row", "col", "card", "accordion",
})

#: 容器型组件：子节点字段名固定为 `items`。
GENUI_CONTAINER_TYPES = frozenset({"row", "col", "card", "accordion"})

#: 允许的动作类型（宿主已实现的闭包，**不含** endpoint/URL/method/capability）。
GENUI_ACTION_KINDS = frozenset({"ask", "select", "confirm"})

#: 语义色调。
GENUI_TONES = frozenset({"info", "success", "warning", "error"})
GENUI_BADGE_TONES = frozenset({"success", "warn", "danger", "accent"})
GENUI_TEXT_SIZES = frozenset({"display", "h1", "h2", "h3", "body", "muted", "caption"})

#: 资源上限：超过就截断/降级（顶层整体超限才整块丢弃）。
MAX_NODES = 100
MAX_DEPTH = 6
MAX_TEXT_CHARS = 4000
MAX_SPEC_BYTES = 64 * 1024
MAX_ARRAY_ITEMS = 100
MAX_TABLE_COLS = 12
MAX_ACTION_PAYLOAD_CHARS = 2000
#: `select` 动作的选项上限（与前端 / genui_contract.json 同值）：太多选项在卡片里读不过来。
MAX_SELECT_OPTIONS = 6
#: 单个选项文案上限。
MAX_OPTION_CHARS = 40

#: 原型污染类危险键：递归剔除，不作为任何语义字段。
FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})

_SCALAR_TYPES = (str, int, float, bool)


def _as_text(value: Any) -> str:
    """把任意叶子值转成可展示文本（数字保留，容器/None 给空串）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def sanitize_spec(raw: Any, *, source: str = "") -> tuple[dict[str, Any] | None, list[str]]:
    """**过渡薄壳**（B2）：按当前注册的 `display_spec` 契约清洗。

    实现在 `clipwright/services/display_spec/sanitizer.py`（契约驱动、通用、无产品字面量）。
    本函数只为兼容 `schema/requirements.py` 的 validator 与既有测试的 import 而存在：
    **不得**在新增的生产写入路径上继续调用；B3 会连同本文件一起删除。
    契约缺失（插件未加载/被禁用）时返回整块不可用 + 可读原因，不抛异常。
    """
    from clipwright.services.display_spec import get_contract
    from clipwright.services.display_spec.sanitizer import sanitize_display_spec

    contract = get_contract("brief")
    if contract is None:
        return None, ["未注册任何 display_spec 契约（插件未加载或被禁用）"]
    return sanitize_display_spec(raw, contract, source=source)


# ── 确定性构造器（模型不给 spec 时的兜底；不调模型、不猜）───────────────


def _self_sanitize(spec: dict[str, Any]) -> dict[str, Any]:
    """过渡期（B2）：契约可用就按契约洗一遍；不可用则**原样返回**。

    节点是本文件代码生成的（不是模型输入），所以没有契约时的"不洗"不构成信任问题；
    真正的写入闸仍在 `sanitize_spec()` / 服务层装配点。B3 把两个构造器整体搬进插件
    配方后，本函数与两个构造器一起删除。
    """
    cleaned, _ = sanitize_spec(spec)
    return cleaned if cleaned is not None else spec


def build_brief_genui(brief: dict[str, Any]) -> dict[str, Any] | None:
    """从创意简报字段生成一份只读 spec（权威字段仍是 `CreativeBrief`）。"""
    if not isinstance(brief, dict) or not brief:
        return None
    title = _as_text(brief.get("title")).strip()
    overview = _as_text(brief.get("overview")).strip()
    pairs = [
        ("目标受众", _as_text(brief.get("target_audience"))),
        ("核心信息", _as_text(brief.get("core_message"))),
        ("风格方向", _as_text(brief.get("style_direction"))),
        ("结构建议", _as_text(brief.get("structure_suggestion"))),
        ("预估时长", _as_text(brief.get("duration_estimate"))),
    ]
    rows = [[k, v] for k, v in pairs if v.strip()]
    items: list[dict[str, Any]] = []
    if title:
        items.append({"type": "text", "content": title, "size": "h2"})
    if overview:
        items.append({"type": "text", "content": overview, "size": "body"})
    if rows:
        items.append({"type": "table", "columns": ["维度", "内容"], "rows": rows})
    key_elements = [x for x in (brief.get("key_elements") or []) if isinstance(x, str) and x.strip()]
    if key_elements:
        items.append({"type": "text", "content": "必须出现的关键元素", "size": "h3"})
        items.append({"type": "list", "items": key_elements[:20]})
    special = [x for x in (brief.get("special_requirements") or []) if isinstance(x, str) and x.strip()]
    if special:
        items.append({
            "type": "callout", "tone": "warning", "title": "特殊要求与红线",
            "content": "；".join(special[:10]),
        })
    if not items:
        return None
    return _self_sanitize({"version": GENUI_VERSION, "title": "创意简报", "items": items})


def build_plan_genui(plan: dict[str, Any], scenes: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """从规划书（权威 plan 或场景列表）生成一份只读 spec。"""
    rows: list[list[str]] = []
    source_scenes = scenes if isinstance(scenes, list) else []
    if not source_scenes and isinstance(plan, dict):
        raw = plan.get("raw_scenes")
        if isinstance(raw, list):
            source_scenes = raw
    total = 0.0
    for idx, scene in enumerate(source_scenes[:MAX_ARRAY_ITEMS], 1):
        if not isinstance(scene, dict):
            continue
        try:
            dur = float(scene.get("duration_sec") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        total += dur
        keywords = scene.get("keywords")
        kw = "、".join(str(k) for k in keywords[:3]) if isinstance(keywords, list) else ""
        rows.append([str(idx), _as_text(scene.get("title")), f"{dur:.0f}s", kw])

    items: list[dict[str, Any]] = []
    scene_count_value = str(len(rows)) if rows else _as_text(plan.get("scene_count") or "")
    try:
        total_value = f"{float(plan.get('total_duration_sec') or total):.0f}s"
    except (TypeError, ValueError):
        total_value = f"{total:.0f}s"
    stats = [
        {"type": "stat", "label": "场景数", "value": scene_count_value or "—"},
        {"type": "stat", "label": "总时长", "value": total_value},
    ]
    items.append({"type": "row", "items": stats})
    if rows:
        items.append({"type": "table", "columns": ["#", "场景", "时长", "关键词"], "rows": rows})
    gap = _as_text(plan.get("material_summary")).strip()
    if gap:
        items.append({"type": "callout", "tone": "info", "content": gap})
    if len(items) <= 1 and not rows:
        return None
    return _self_sanitize({"version": GENUI_VERSION, "title": "制作规划书", "items": items})


def load_contract() -> dict[str, Any]:
    """读取**单一真相文件** `genui_contract.json`（本模块常量 / 前端副本 / 自检脚本都对齐它）。

    本模块**不**用它在运行期拼白名单（保持导入期零 I/O）；它只服务两类消费者：
    ① `tests/clipwright/test_genui_spec.py::TestContractSingleSource` 断言常量没漂；
    ② `scripts/verify_provider_pluginization.py` 比对两仓 JSON 是否逐字节一致。
    """
    from pathlib import Path

    path = Path(__file__).with_name("genui_contract.json")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 接线用公共函数（服务层与 Agent 共用，避免两套实现漂移）──────────────


def _log_problems(kind: str, problems: list[str]) -> None:
    """降级原因写日志（本地导入 logger，保持本模块导入期无副作用）。"""
    if not problems:
        return
    try:
        from clipwright.config import logger

        logger.info("%s 的 genui_spec 已降级（前 3 条）：%s", kind, problems[:3])
    except Exception:  # noqa: BLE001 — 日志不可用不影响展示
        pass


def ensure_brief_spec(brief: dict[str, Any]) -> dict[str, Any]:
    """保证简报带一份**可信**的展示 spec；绝不改其它字段。

    模型给了就洗（`sanitize_spec`：未知组件降级、危险键剔除、超限截断）；
    没给/给坏了就用 `build_brief_genui()` 从权威字段确定性补一份。
    spec 只影响展示——它丢了或坏了，简报字段、确认流程、规划书指纹都不受影响（C10）。
    """
    if not isinstance(brief, dict):
        return brief
    spec, problems = sanitize_spec(brief.get("genui"), source="brief")
    _log_problems("创意简报", problems)
    brief["genui"] = spec or (build_brief_genui(brief) or {})
    return brief


def ensure_plan_spec(plan: dict[str, Any], scenes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """规划书 spec **只**用确定性构造器，**不看**模型给的 `genui`。

    规划书是用户确认的对象：展示层若能被模型编造，用户可能"看着漂亮的卡片确认了一份
    markdown 里没有的方案"（PRD §9 / coding plan 0.5-③）。返回写回 `plan["genui"]` 的 spec。
    """
    if not isinstance(plan, dict):
        return {}
    spec = build_plan_genui(plan, list(scenes or [])) or {}
    plan["genui"] = spec
    return spec


def spec_debug_summary(spec: dict[str, Any] | None) -> dict[str, Any]:
    """给日志/自检用的一行摘要（不回给前端）。"""
    if not isinstance(spec, dict):
        return {"present": False}
    items = spec.get("items") or []
    return {
        "present": True,
        "version": spec.get("version"),
        "top_level": len(items) if isinstance(items, list) else 0,
        "bytes": len(json.dumps(spec, ensure_ascii=False)),
    }
