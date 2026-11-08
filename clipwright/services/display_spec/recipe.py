"""声明式卡片配方解释器（插件给配方、engine 执行——这是"能力归插件"的关键机制）。

配方是**纯数据**（JSON），只允许下面 11 个算子；没有 `eval` / `exec` / `getattr` /
`__import__`，也不接受任何可调用对象（插件永远交不出代码给 engine 执行，PRD §3.3）。

算子（语义冻结，**禁止扩展**；扩展要改 coding plan §2.4 并走契约评审）：

| 算子 | 语法 | 语义 |
|---|---|---|
| literal | `"文本"` / `12` / `true` | 原样 |
| field | `{"field":"a.b"}` | 按 `.` 分段 `dict.get`；缺失/非标量 → `""` |
| item | `{"item":"scene.title"}` | 仅 `foreach` 内有效 |
| index_plus_one | `{"index_plus_one":"i"}` | 仅 `foreach` 内有效，输出 `i+1` |
| join | `{"join":{"field":"k","sep":"、","limit":3}}` | list 里只取 str 项后 join |
| pairs | `{"pairs":[{"label":"…","field":"…"}]}` | 生成二维 rows，跳过空值行 |
| foreach | `{"foreach":{...},"row":[…]}` | 逐项求值成二维 rows |
| count | `{"count":"raw_scenes"}` | list 长度（非 list → 0） |
| sum_of | `{"sum_of":{"field":"…","value":"duration_sec"}}` | 数值求和（失败当 0） |
| fmt_sec | `{"fmt_sec":<expr>}` | 数字 → `"{:.0f}s"`；非法 → `"0s"` |
| coalesce | `{"coalesce":[<expr>, …]}` | 第一个非空（`""`/`None`/`[]` 算空，`0` 不算） |

节点级开关：`skip_if_empty`（该字段求值为空则不产出节点）、`when`（`{"field":…, "non_empty":true}`）。
"""
from __future__ import annotations

from typing import Any, Mapping

from .contract import Contract
from .sanitizer import sanitize_display_spec

#: 节点里"值位"的字段名（其余字段按字面量复制）。
_VALUE_FIELDS = ("content", "items", "rows", "value", "label", "delta", "valueLabel", "steps", "current")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _get_path(view: Any, path: str) -> Any:
    """按 `.` 分段取 dict 值；任何非 dict 中间层返回 None（不做属性访问）。"""
    current: Any = view
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _is_empty(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


class RecipeError(ValueError):
    """配方自身非法（未知算子/形态错误）——属于**插件的问题**，engine 只拒绝不猜。"""


def _apply_limit(value: Any, limit_raw: Any) -> Any:
    """`field`/`item` 表达式的可选 `limit`：只截 list（其它类型原样返回）。"""
    try:
        limit = int(limit_raw or 0)
    except (TypeError, ValueError):
        limit = 0
    if isinstance(value, (list, tuple)) and limit > 0:
        return list(value)[:limit]
    return value


def _eval(expr: Any, ctx: dict[str, Any]) -> Any:
    """求值一个配方表达式。字符串/数字/布尔是字面量；dict 必须是已知算子。"""
    if isinstance(expr, (str, int, float, bool)) or expr is None:
        return expr
    if isinstance(expr, list):
        return [_eval(x, ctx) for x in expr]
    if not isinstance(expr, Mapping):
        raise RecipeError(f"不支持的表达式类型：{type(expr).__name__}")

    keys = set(expr.keys())
    if "field" in keys and keys <= {"field", "limit"}:
        value = _get_path(ctx.get("view"), str(expr["field"]))
        return _apply_limit(value, expr.get("limit"))
    if "item" in keys and keys <= {"item", "limit"}:
        value = _get_path(ctx.get("item"), str(expr["item"]))
        return _apply_limit(value, expr.get("limit"))
    if keys == {"index_plus_one"}:
        name = str(expr["index_plus_one"])
        index = ctx.get("index", {}).get(name)
        return index + 1 if isinstance(index, int) else ""
    if keys == {"count"}:
        target = _get_path(ctx.get("view"), str(expr["count"]))
        return len(target) if isinstance(target, (list, tuple)) else 0
    if keys == {"sum_of"}:
        spec = expr["sum_of"] if isinstance(expr["sum_of"], Mapping) else {}
        rows = _get_path(ctx.get("view"), str(spec.get("field") or ""))
        value_key = str(spec.get("value") or "")
        total = 0.0
        if isinstance(rows, (list, tuple)):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                try:
                    total += float(row.get(value_key) or 0)
                except (TypeError, ValueError):
                    continue
        return total
    if keys == {"fmt_sec"}:
        value = _eval(expr["fmt_sec"], ctx)
        try:
            return f"{float(value):.0f}s"
        except (TypeError, ValueError):
            return "0s"
    if keys == {"coalesce"}:
        candidates = expr["coalesce"]
        if not isinstance(candidates, list):
            raise RecipeError("coalesce 必须是数组")
        for candidate in candidates:
            value = _eval(candidate, ctx)
            if not _is_empty(value):
                return value
        return ""
    if keys == {"join"}:
        return _eval_join(expr["join"], ctx)
    if keys == {"pairs"}:
        return _eval_pairs(expr["pairs"], ctx)
    if keys == {"foreach", "row"}:
        return _eval_foreach(expr, ctx)
    raise RecipeError(f"未知配方算子：{sorted(keys)}")


def _eval_join(spec: Any, ctx: dict[str, Any]) -> str:
    if not isinstance(spec, Mapping):
        raise RecipeError("join 必须是对象")
    target = _eval({k: spec[k] for k in spec if k in ("field", "item")} or {}, ctx)
    separator = str(spec.get("sep") or "、")
    try:
        limit = int(spec.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    if not isinstance(target, (list, tuple)):
        return ""
    items = [str(x) for x in target if isinstance(x, str) and x.strip()]
    if limit > 0:
        items = items[:limit]
    return separator.join(items)


def _eval_pairs(spec: Any, ctx: dict[str, Any]) -> list[list[str]]:
    if not isinstance(spec, list):
        raise RecipeError("pairs 必须是数组")
    rows: list[list[str]] = []
    for pair in spec:
        if not isinstance(pair, Mapping):
            raise RecipeError("pairs 的条目必须是对象")
        value = _as_text(_get_path(ctx.get("view"), str(pair.get("field") or ""))).strip()
        if value:
            rows.append([str(pair.get("label") or ""), value])
    return rows


def _eval_foreach(spec: Mapping[str, Any], ctx: dict[str, Any]) -> list[list[str]]:
    loop = spec.get("foreach")
    row_expr = spec.get("row")
    if not isinstance(loop, Mapping) or not isinstance(row_expr, list):
        raise RecipeError("foreach 需要 {foreach:{field,item,index}, row:[…]}")
    rows_raw = _get_path(ctx.get("view"), str(loop.get("field") or ""))
    if not isinstance(rows_raw, (list, tuple)):
        return []
    item_name = str(loop.get("item") or "item")
    index_name = str(loop.get("index") or "i")
    out: list[list[str]] = []
    for index, item in enumerate(rows_raw):
        child = {
            "view": ctx.get("view"),
            "item": {item_name: item},
            "index": {index_name: index},
        }
        out.append([_as_text(_eval(cell, child)) for cell in row_expr])
    return out


def _node_when(node: Mapping[str, Any], ctx: dict[str, Any]) -> bool:
    """`when`：目前只支持 `{field, non_empty}`；其它形态视为 False（不产出）。"""
    when = node.get("when")
    if when is None:
        return True
    if not isinstance(when, Mapping):
        return False
    value = _get_path(ctx.get("view"), str(when.get("field") or ""))
    if when.get("non_empty"):
        return not _is_empty(value)
    return not _is_empty(value)


def _build_node(node: Any, ctx: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(node, Mapping):
        raise RecipeError("节点必须是对象")
    if not _node_when(node, ctx):
        return None
    built: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("when", "skip_if_empty", "type"):
            continue
        if key == "items" and isinstance(value, list):
            children = [c for c in (_build_node(child, ctx) for child in value) if c is not None]
            built["items"] = children
            continue
        if key in _VALUE_FIELDS:
            built[key] = _eval(value, ctx)
        else:
            built[key] = value
    built["type"] = str(node.get("type") or "")
    skip_field = node.get("skip_if_empty")
    if isinstance(skip_field, str) and _is_empty(built.get(skip_field)):
        return None
    return built


def build_spec_from_recipe(
    recipe: Mapping[str, Any] | None,
    view: Mapping[str, Any] | None,
    contract: Contract,
    *,
    title: str = "",
) -> dict[str, Any] | None:
    """按配方从源数据生成 spec；节点产出后**必须**过一遍契约清洗（不可信输入也走同一闸）。

    返回 `None` 表示配方没产出任何可用节点（宿主回落到原卡片/文本）。
    """
    if not isinstance(recipe, Mapping):
        return None
    nodes = recipe.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    ctx = {"view": dict(view or {})}
    items = [n for n in (_build_node(node, ctx) for node in nodes) if n is not None]
    if not items:
        return None
    spec_title = title or str(recipe.get("title") or "")
    raw_spec: dict[str, Any] = {"version": contract.version, "items": items}
    if spec_title:
        raw_spec["title"] = spec_title
    spec, _problems = sanitize_display_spec(raw_spec, contract)
    return spec
