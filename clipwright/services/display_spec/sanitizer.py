"""契约驱动的**通用**展示 spec 清洗器（信任边界在 engine，不在插件）。

为什么清洗器不能由插件提供（PRD §3.3）：

- 插件 A 若自带 `sanitize=lambda raw: raw`，后端就不再可信：`html` / 未知组件会直接入库，
  任何**新消费者**（导出、缓存、日志、将来的第三方 API）都吃未消毒数据；
- 前端第二道闸能拦一部分，但那会把 #26 的"双道闸"退化成单道；
- 进程内插件与 engine 同权限，没有超时机制（`loader.py` 无此路径）。

所以本模块：

- **只认契约**（`contract.components / actions / tones / badge_tones / text_sizes / limits`），
  文件里没有任何产品或组件名字面量（验收：coding plan B2-T2.3 的那条大小写敏感 grep 必须 0 命中）；
- **从不执行插件代码**，只读它声明的数据；
- 未知组件降级为占位文本（兄弟节点保留），非法动作丢弃，超限截断/整块丢弃。
"""
from __future__ import annotations

import json
from typing import Any

from .contract import Contract

#: 原型污染类危险键：递归剔除，不作为任何语义字段（通用安全常量，与产品无关）。
FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})

_SCALAR_TYPES = (str, int, float, bool)


def sanitize_display_spec(
    raw: Any,
    contract: Contract,
    *,
    source: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    """把「可能是模型编的」spec 按 `contract` 洗成可信 spec。

    返回 ``(spec|None, problems)``：``None`` 表示整块不可用（宿主回落原卡片/文本），
    ``problems`` 是可诊断的降级原因（写日志，不回给用户当错误弹窗）。
    """
    problems: list[str] = []
    if raw in (None, "", {}, []):
        return None, problems
    if not isinstance(raw, dict):
        return None, ["spec 顶层不是对象"]

    # ── 契约里的枚举与上限（全部来自插件声明，本模块不硬编码）──────────
    version_expected = int(contract.version)
    components = set(contract.components)
    actions = set(contract.actions)
    tones = set(contract.tones)
    badge_tones = set(contract.badge_tones)
    text_sizes = set(contract.text_sizes)
    max_nodes = contract.limit("max_nodes", 100)
    max_depth = contract.limit("max_depth", 6)
    max_text_chars = contract.limit("max_text_chars", 4000)
    max_spec_bytes = contract.limit("max_spec_bytes", 65536)
    max_array_items = contract.limit("max_array_items", 100)
    max_table_cols = contract.limit("max_table_cols", 12)
    max_action_payload_chars = contract.limit("max_action_payload_chars", 2000)
    max_select_options = contract.limit("max_select_options", 6)
    max_option_chars = contract.limit("max_option_chars", 40)

    # ── 叶子工具（闭包捕获契约上限）──────────────────────────────
    def as_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)):
            return str(value)
        return ""

    def clip(text: str, limit: int | None = None) -> str:
        cap = max_text_chars if limit is None else limit
        return text if len(text) <= cap else text[:cap] + "…（已截断）"

    def clean_payload(value: Any, depth: int = 0) -> Any:
        """动作载荷只留标量与浅层容器（不许函数/对象/超大字符串）。"""
        if depth > 2:
            return None
        if isinstance(value, _SCALAR_TYPES):
            return clip(value, 500) if isinstance(value, str) else value
        if isinstance(value, list):
            return [clean_payload(v, depth + 1) for v in value[:20]]
        if isinstance(value, dict):
            return {
                str(k): clean_payload(v, depth + 1)
                for k, v in list(value.items())[:20]
                if str(k) not in FORBIDDEN_KEYS
            }
        return None

    def clean_select_options(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        raw_options = payload.get("options")
        if not isinstance(raw_options, list):
            return []
        out: list[str] = []
        for item in raw_options[: max_select_options * 2]:
            if not isinstance(item, str):
                continue
            # 选项是按钮文案：**纯截断**，不加「…（已截断）」标记（按钮里出现标记是噪音）
            text = item.strip()[:max_option_chars]
            if text and text not in out:
                out.append(text)
            if len(out) >= max_select_options:
                break
        return out

    def clean_action(node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        kind = as_text(node.get("type")).strip().lower()
        if kind not in actions:
            problems.append(f"动作类型不在白名单：{kind or '空'}")
            return None
        action: dict[str, Any] = {"type": kind}
        label = as_text(node.get("label")).strip()
        if label:
            action["label"] = clip(label, 80)
        payload = clean_payload(node.get("payload"))
        if isinstance(payload, dict) and payload:
            action["payload"] = payload
        if kind == "select":
            options = clean_select_options(payload)
            if not options:
                problems.append("select 没有可用的 payload.options，动作已丢弃")
                return None
            action["payload"] = {"options": options}
        if len(json.dumps(action, ensure_ascii=False)) > max_action_payload_chars:
            problems.append("动作载荷超长，已丢弃")
            return {"type": kind}
        return action

    def clean_string_list(raw_list: Any, field: str) -> list[str]:
        if not isinstance(raw_list, list):
            if raw_list is not None:
                problems.append(f"{field} 不是数组，已忽略")
            return []
        out = [clip(as_text(x)) for x in raw_list[:max_array_items]]
        if len(raw_list) > max_array_items:
            problems.append(f"{field} 超过 {max_array_items} 项，已截断")
        return [x for x in out if x]

    def clean_list_items(raw_items: Any) -> list[Any]:
        if not isinstance(raw_items, list):
            problems.append("list.items 不是数组，已忽略")
            return []
        out: list[Any] = []
        for item in raw_items[:max_array_items]:
            if isinstance(item, str):
                text = clip(item)
                if text:
                    out.append(text)
            elif isinstance(item, dict):
                title = clip(as_text(item.get("title") or item.get("label")))
                desc = clip(as_text(item.get("desc") or item.get("description")))
                if title or desc:
                    out.append({"title": title, "desc": desc})
            else:
                problems.append("list 条目既不是字符串也不是对象，已丢弃")
        return out

    def clean_table(node: dict[str, Any]) -> dict[str, Any]:
        columns = clean_string_list(node.get("columns"), "table.columns")
        if len(columns) > max_table_cols:
            problems.append(f"table 列数超过 {max_table_cols}，已截断")
            columns = columns[:max_table_cols]
        raw_rows = node.get("rows")
        rows: list[list[str]] = []
        if isinstance(raw_rows, list):
            if len(raw_rows) > max_array_items:
                problems.append(f"table 行数超过 {max_array_items}，已截断")
            for row in raw_rows[:max_array_items]:
                if not isinstance(row, (list, tuple)):
                    continue
                cell_cap = len(columns) or max_table_cols
                cells = [clip(as_text(cell), 500) for cell in list(row)[:cell_cap]]
                if cells:
                    rows.append(cells)
        else:
            problems.append("table.rows 不是数组，已忽略")
        return {"type": "table", "columns": columns, "rows": rows}

    def clean_steps(node: dict[str, Any]) -> dict[str, Any]:
        raw_steps = node.get("steps")
        steps: list[dict[str, Any]] = []
        if isinstance(raw_steps, list):
            for item in raw_steps[:max_array_items]:
                if isinstance(item, str):
                    if item.strip():
                        steps.append({"title": clip(item)})
                elif isinstance(item, dict):
                    title = clip(as_text(item.get("title")))
                    if title:
                        step: dict[str, Any] = {"title": title}
                        desc = clip(as_text(item.get("desc")))
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

    def clean_node(raw_node: Any, depth: int, counter: list[int]) -> dict[str, Any] | None:
        if depth > max_depth:
            problems.append(f"嵌套超过 {max_depth} 层，以下节点已丢弃")
            return None
        if not isinstance(raw_node, dict):
            problems.append("节点不是对象，已丢弃")
            return None
        for key in list(raw_node):
            if str(key) in FORBIDDEN_KEYS:
                problems.append(f"剔除危险键：{key}")
                raw_node.pop(key, None)
        kind = as_text(raw_node.get("type")).strip().lower()
        if kind not in components:
            problems.append(f"未知组件已降级为占位：{kind or '空'}")
            return {"type": "text", "content": f"（暂不支持的组件：{kind or '空'}）", "size": "muted"}

        counter[0] += 1
        if counter[0] > max_nodes:
            return None

        node: dict[str, Any]
        if kind == "text":
            node = {"type": "text", "content": clip(as_text(raw_node.get("content")))}
            size = as_text(raw_node.get("size")).strip().lower()
            if size in text_sizes:
                node["size"] = size
            tone = as_text(raw_node.get("tone")).strip().lower()
            if tone in tones:
                node["tone"] = tone
        elif kind == "list":
            node = {"type": "list", "items": clean_list_items(raw_node.get("items"))}
        elif kind == "callout":
            tone = as_text(raw_node.get("tone")).strip().lower()
            node = {
                "type": "callout",
                "tone": tone if tone in tones else "info",
                "content": clip(as_text(raw_node.get("content"))),
            }
            title = clip(as_text(raw_node.get("title")), 120)
            if title:
                node["title"] = title
        elif kind == "badge":
            tone = as_text(raw_node.get("tone")).strip().lower()
            node = {"type": "badge", "label": clip(as_text(raw_node.get("label")), 60)}
            if tone in badge_tones:
                node["tone"] = tone
        elif kind == "stat":
            node = {
                "type": "stat",
                "label": clip(as_text(raw_node.get("label")), 80),
                "value": clip(as_text(raw_node.get("value")), 80),
            }
            delta = clip(as_text(raw_node.get("delta")), 40)
            if delta:
                node["delta"] = delta
        elif kind == "progress":
            raw_value = raw_node.get("value")
            value = float(raw_value) if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) else 0.0
            node = {"type": "progress", "value": max(0.0, min(100.0, value))}
            label = clip(as_text(raw_node.get("label")), 80)
            if label:
                node["label"] = label
            value_label = clip(as_text(raw_node.get("valueLabel")), 40)
            if value_label:
                node["valueLabel"] = value_label
        elif kind == "table":
            node = clean_table(raw_node)
        elif kind == "steps":
            node = clean_steps(raw_node)
        elif kind == "divider":
            node = {"type": "divider"}
            label = clip(as_text(raw_node.get("label")), 80)
            if label:
                node["label"] = label
        elif kind in ("row", "col"):
            children = raw_node.get("items")
            node = {"type": kind, "items": []}
            if isinstance(children, list):
                for child in children:
                    cleaned = clean_node(child, depth + 1, counter)
                    if cleaned is not None:
                        node["items"].append(cleaned)
        elif kind == "card":
            node = {"type": "card", "items": []}
            title = clip(as_text(raw_node.get("title")), 120)
            if title:
                node["title"] = title
            tone = as_text(raw_node.get("tone")).strip().lower()
            if tone in tones:
                node["tone"] = tone
            children = raw_node.get("items")
            if isinstance(children, list):
                for child in children:
                    cleaned = clean_node(child, depth + 1, counter)
                    if cleaned is not None:
                        node["items"].append(cleaned)
        else:  # accordion
            node = {"type": "accordion", "items": []}
            children = raw_node.get("items")
            if isinstance(children, list):
                for child in children[:max_array_items]:
                    if not isinstance(child, dict):
                        continue
                    title = clip(as_text(child.get("title")), 120)
                    inner: list[dict[str, Any]] = []
                    grand = child.get("items")
                    if isinstance(grand, list):
                        for g in grand:
                            cleaned = clean_node(g, depth + 1, counter)
                            if cleaned is not None:
                                inner.append(cleaned)
                    node["items"].append({"title": title, "items": inner})

        action = clean_action(raw_node.get("action"))
        if action is not None:
            node["action"] = action
        return node

    # ── 顶层：版本 → items → 清洗 → 体量 ──────────────────────────
    version = raw.get("version", version_expected)
    if isinstance(version, bool) or not isinstance(version, (int, str)):
        problems.append(f"版本字段非法：{version!r}，按 {version_expected} 处理")
        version = version_expected
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        version_int = version_expected
    if version_int != version_expected:
        return None, [f"不支持的 spec 版本：{version_int}（当前 {version_expected}）"]

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        return None, ["spec.items 缺失或为空"]

    counter = [0]
    items: list[dict[str, Any]] = []
    for entry in items_raw:
        cleaned = clean_node(entry, 1, counter)
        if cleaned is not None:
            items.append(cleaned)
    if not items:
        return None, problems + ["清洗后没有任何可用节点"]
    if counter[0] > max_nodes:
        problems.append(f"节点数超过 {max_nodes}，多余节点已丢弃")

    spec: dict[str, Any] = {"version": version_expected, "items": items}
    title = clip(as_text(raw.get("title")), 120)
    if title:
        spec["title"] = title
    if source:
        spec["source"] = source
    # 体量检查放在**清洗之后**：模型偶发塞一个不可 JSON 化的值不该拖垮整块，
    # 清洗阶段已经把它们丢掉了；这里只看最终要下发的字节数（字符数近似）。
    try:
        size = len(json.dumps(spec, ensure_ascii=False))
    except (TypeError, ValueError):
        return None, problems + ["spec 清洗后仍无法序列化"]
    if size > max_spec_bytes:
        return None, problems + [f"spec 超过 {max_spec_bytes} 字节（{size}），整块丢弃"]
    return spec, problems
