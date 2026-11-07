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


def _clean_payload(value: Any, depth: int = 0) -> Any:
    """动作载荷只留标量与浅层容器（不许函数/对象/超大字符串）。"""
    if depth > 2:
        return None
    if isinstance(value, _SCALAR_TYPES):
        return _clip(value, 500) if isinstance(value, str) else value
    if isinstance(value, list):
        return [_clean_payload(v, depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return {
            str(k): _clean_payload(v, depth + 1)
            for k, v in list(value.items())[:20]
            if str(k) not in FORBIDDEN_KEYS
        }
    return None


def _clean_action(raw: Any, problems: list[str]) -> dict[str, Any] | None:
    """动作白名单：`type` 必须是 ask/select/confirm，载荷只留标量。"""
    if not isinstance(raw, dict):
        return None
    kind = _as_text(raw.get("type")).strip().lower()
    if kind not in GENUI_ACTION_KINDS:
        problems.append(f"动作类型不在白名单：{kind or '空'}")
        return None
    action: dict[str, Any] = {"type": kind}
    label = _as_text(raw.get("label")).strip()
    if label:
        action["label"] = _clip(label, 80)
    payload = _clean_payload(raw.get("payload"))
    if isinstance(payload, dict) and payload:
        action["payload"] = payload
    if len(json.dumps(action, ensure_ascii=False)) > MAX_ACTION_PAYLOAD_CHARS:
        problems.append("动作载荷超长，已丢弃")
        return {"type": kind}
    return action


def _clean_string_list(raw: Any, problems: list[str], field: str) -> list[str]:
    if not isinstance(raw, list):
        if raw is not None:
            problems.append(f"{field} 不是数组，已忽略")
        return []
    out = [_clip(_as_text(x)) for x in raw[:MAX_ARRAY_ITEMS]]
    if len(raw) > MAX_ARRAY_ITEMS:
        problems.append(f"{field} 超过 {MAX_ARRAY_ITEMS} 项，已截断")
    return [x for x in out if x]


def _clean_list_items(raw: Any, problems: list[str]) -> list[Any]:
    """`list` 组件的条目：字符串或 `{title, desc}`；其他形态丢弃。"""
    if not isinstance(raw, list):
        problems.append("list.items 不是数组，已忽略")
        return []
    out: list[Any] = []
    for item in raw[:MAX_ARRAY_ITEMS]:
        if isinstance(item, str):
            text = _clip(item)
            if text:
                out.append(text)
        elif isinstance(item, dict):
            title = _clip(_as_text(item.get("title") or item.get("label")))
            desc = _clip(_as_text(item.get("desc") or item.get("description")))
            if title or desc:
                out.append({"title": title, "desc": desc})
        else:
            problems.append("list 条目既不是字符串也不是对象，已丢弃")
    return out


def _clean_table(node: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    columns = _clean_string_list(node.get("columns"), problems, "table.columns")
    if len(columns) > MAX_TABLE_COLS:
        problems.append(f"table 列数超过 {MAX_TABLE_COLS}，已截断")
        columns = columns[:MAX_TABLE_COLS]
    rows_raw = node.get("rows")
    rows: list[list[str]] = []
    if isinstance(rows_raw, list):
        if len(rows_raw) > MAX_ARRAY_ITEMS:
            problems.append(f"table 行数超过 {MAX_ARRAY_ITEMS}，已截断")
        for row in rows_raw[:MAX_ARRAY_ITEMS]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [_clip(_as_text(cell), 500) for cell in list(row)[: len(columns) or MAX_TABLE_COLS]]
            if cells:
                rows.append(cells)
    else:
        problems.append("table.rows 不是数组，已忽略")
    return {"type": "table", "columns": columns, "rows": rows}


def _clean_steps(node: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    raw = node.get("steps")
    steps: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw[:MAX_ARRAY_ITEMS]:
            if isinstance(item, str):
                if item.strip():
                    steps.append({"title": _clip(item)})
            elif isinstance(item, dict):
                title = _clip(_as_text(item.get("title")))
                if title:
                    step: dict[str, Any] = {"title": title}
                    desc = _clip(_as_text(item.get("desc")))
                    if desc:
                        step["desc"] = desc
                    steps.append(step)
    else:
        problems.append("steps.steps 不是数组，已忽略")
    out: dict[str, Any] = {"type": "steps", "steps": steps}
    current = node.get("current")
    if isinstance(current, int) and not isinstance(current, bool) and current >= 0:
        out["current"] = min(current, max(len(steps) - 1, 0))
    return out


def _clean_node(raw: Any, problems: list[str], depth: int, counter: list[int]) -> dict[str, Any] | None:
    """清洗单个节点；返回 None 表示这个节点整体丢弃。"""
    if depth > MAX_DEPTH:
        problems.append(f"嵌套超过 {MAX_DEPTH} 层，以下节点已丢弃")
        return None
    if not isinstance(raw, dict):
        problems.append("节点不是对象，已丢弃")
        return None
    for key in list(raw):
        if str(key) in FORBIDDEN_KEYS:
            problems.append(f"剔除危险键：{key}")
            raw.pop(key, None)
    kind = _as_text(raw.get("type")).strip().lower()
    if kind not in GENUI_COMPONENT_TYPES:
        problems.append(f"未知组件已降级为占位：{kind or '空'}")
        return {"type": "text", "content": f"（暂不支持的组件：{kind or '空'}）", "size": "muted"}

    counter[0] += 1
    if counter[0] > MAX_NODES:
        return None

    node: dict[str, Any]
    if kind == "text":
        node = {"type": "text", "content": _clip(_as_text(raw.get("content")))}
        size = _as_text(raw.get("size")).strip().lower()
        if size in GENUI_TEXT_SIZES:
            node["size"] = size
        tone = _as_text(raw.get("tone")).strip().lower()
        if tone in GENUI_TONES:
            node["tone"] = tone
    elif kind == "list":
        node = {"type": "list", "items": _clean_list_items(raw.get("items"), problems)}
    elif kind == "callout":
        tone = _as_text(raw.get("tone")).strip().lower()
        node = {
            "type": "callout",
            "tone": tone if tone in GENUI_TONES else "info",
            "content": _clip(_as_text(raw.get("content"))),
        }
        title = _clip(_as_text(raw.get("title")), 120)
        if title:
            node["title"] = title
    elif kind == "badge":
        tone = _as_text(raw.get("tone")).strip().lower()
        node = {"type": "badge", "label": _clip(_as_text(raw.get("label")), 60)}
        if tone in GENUI_BADGE_TONES:
            node["tone"] = tone
    elif kind == "stat":
        node = {
            "type": "stat",
            "label": _clip(_as_text(raw.get("label")), 80),
            "value": _clip(_as_text(raw.get("value")), 80),
        }
        delta = _clip(_as_text(raw.get("delta")), 40)
        if delta:
            node["delta"] = delta
    elif kind == "progress":
        raw_value = raw.get("value")
        value = float(raw_value) if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) else 0.0
        node = {"type": "progress", "value": max(0.0, min(100.0, value))}
        label = _clip(_as_text(raw.get("label")), 80)
        if label:
            node["label"] = label
        value_label = _clip(_as_text(raw.get("valueLabel")), 40)
        if value_label:
            node["valueLabel"] = value_label
    elif kind == "table":
        node = _clean_table(raw, problems)
    elif kind == "steps":
        node = _clean_steps(raw, problems)
    elif kind == "divider":
        node = {"type": "divider"}
        label = _clip(_as_text(raw.get("label")), 80)
        if label:
            node["label"] = label
    elif kind in ("row", "col"):
        children = raw.get("items")
        node = {"type": kind, "items": []}
        if isinstance(children, list):
            for child in children:
                cleaned = _clean_node(child, problems, depth + 1, counter)
                if cleaned is not None:
                    node["items"].append(cleaned)
    elif kind == "card":
        node = {"type": "card", "items": []}
        title = _clip(_as_text(raw.get("title")), 120)
        if title:
            node["title"] = title
        tone = _as_text(raw.get("tone")).strip().lower()
        if tone in GENUI_TONES:
            node["tone"] = tone
        children = raw.get("items")
        if isinstance(children, list):
            for child in children:
                cleaned = _clean_node(child, problems, depth + 1, counter)
                if cleaned is not None:
                    node["items"].append(cleaned)
    else:  # accordion
        node = {"type": "accordion", "items": []}
        children = raw.get("items")
        if isinstance(children, list):
            for child in children[:MAX_ARRAY_ITEMS]:
                if not isinstance(child, dict):
                    continue
                title = _clip(_as_text(child.get("title")), 120)
                inner: list[dict[str, Any]] = []
                grand = child.get("items")
                if isinstance(grand, list):
                    for g in grand:
                        cleaned = _clean_node(g, problems, depth + 1, counter)
                        if cleaned is not None:
                            inner.append(cleaned)
                node["items"].append({"title": title, "items": inner})

    action = _clean_action(raw.get("action"), problems)
    if action is not None:
        node["action"] = action
    return node


def sanitize_spec(raw: Any, *, source: str = "") -> tuple[dict[str, Any] | None, list[str]]:
    """把「可能是模型编的」spec 洗成可信 spec。

    返回 ``(spec|None, problems)``：``None`` 表示整块不可用（前端回落到原卡片/文本），
    ``problems`` 是可诊断的降级原因（写日志，不回给用户当错误弹窗）。
    """
    problems: list[str] = []
    if raw in (None, "", {}, []):
        return None, problems
    if not isinstance(raw, dict):
        return None, ["spec 顶层不是对象"]

    version = raw.get("version", GENUI_VERSION)
    if isinstance(version, bool) or not isinstance(version, (int, str)):
        problems.append(f"版本字段非法：{version!r}，按 {GENUI_VERSION} 处理")
        version = GENUI_VERSION
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        version_int = GENUI_VERSION
    if version_int != GENUI_VERSION:
        return None, [f"不支持的 spec 版本：{version_int}（当前 {GENUI_VERSION}）"]

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        return None, ["spec.items 缺失或为空"]

    counter = [0]
    items: list[dict[str, Any]] = []
    for entry in items_raw:
        cleaned = _clean_node(entry, problems, 1, counter)
        if cleaned is not None:
            items.append(cleaned)
    if not items:
        return None, problems + ["清洗后没有任何可用节点"]
    if counter[0] > MAX_NODES:
        problems.append(f"节点数超过 {MAX_NODES}，多余节点已丢弃")

    spec: dict[str, Any] = {"version": GENUI_VERSION, "items": items}
    title = _clip(_as_text(raw.get("title")), 120)
    if title:
        spec["title"] = title
    if source:
        spec["source"] = source
    # 体量检查放在**清洗之后**：模型偶发塞一个不可 JSON 化的值（函数/对象）不该拖垮整块，
    # 清洗阶段已经把它们丢掉了；这里只看最终要下发的字节数。
    try:
        size = len(json.dumps(spec, ensure_ascii=False))
    except (TypeError, ValueError):
        return None, problems + ["spec 清洗后仍无法序列化"]
    if size > MAX_SPEC_BYTES:
        return None, problems + [f"spec 超过 {MAX_SPEC_BYTES} 字节（{size}），整块丢弃"]
    return spec, problems


# ── 确定性构造器（模型不给 spec 时的兜底；不调模型、不猜）───────────────


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
    spec, _ = sanitize_spec({"version": GENUI_VERSION, "title": "创意简报", "items": items})
    return spec


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
    spec, _ = sanitize_spec({"version": GENUI_VERSION, "title": "制作规划书", "items": items})
    return spec


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
