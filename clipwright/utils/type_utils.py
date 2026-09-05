"""Python 类型注解 → JSON Schema 类型的共享工具函数。

被 clipwright/tool/ 和 clipwright/skill/ 共同使用。
"""

from __future__ import annotations

import inspect
import re
import typing
from typing import Any


def annotation_to_json_type(annotation: Any) -> str:
    """将 Python 类型注解映射为 JSON Schema 类型名。

    支持：str, int, float, bool, list, dict, Optional[X],
    list[X], dict[K,V] 等常见形式。

    T10: int → "integer"（而非 "number"），保留整数语义。
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return "string"

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is not None:
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            return annotation_to_json_type(non_none[0]) if non_none else "string"
        if origin in (list, tuple, set, typing.List, typing.Tuple, typing.Set):
            return "array"
        if origin in (dict, typing.Dict):
            return "object"

    if isinstance(annotation, type):
        if issubclass(annotation, str):
            return "string"
        if issubclass(annotation, bool):
            return "boolean"
        if issubclass(annotation, int):
            return "integer"
        if issubclass(annotation, float):
            return "number"
        if issubclass(annotation, (list, tuple, set)):
            return "array"
        if issubclass(annotation, dict):
            return "object"

    name = getattr(annotation, "__name__", str(annotation))
    mapping = {
        "str": "string", "string": "string",
        "int": "integer", "float": "number", "number": "number",
        "bool": "boolean", "boolean": "boolean",
        "list": "array", "tuple": "array", "array": "array",
        "dict": "object", "object": "object",
    }
    return mapping.get(name.lower(), "string") if isinstance(name, str) else "string"


def _parse_docstring_args(docstring: str) -> dict[str, str]:
    """从 Google 风格 docstring 的 Args 段提取参数描述。

    格式: ``param_name: description text``
    返回 {param_name: description}。
    """
    result: dict[str, str] = {}
    if not docstring:
        return result
    in_args = False
    current_param = ""
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.startswith("Args:") or stripped.startswith("Arguments:"):
            in_args = True
            continue
        if in_args:
            if stripped.startswith(("Returns:", "Raises:", "Yields:", "Examples:")) or (
                stripped.startswith("```")
            ):
                break
            m = re.match(r"^(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)", stripped)
            if m:
                current_param = m.group(1)
                result[current_param] = m.group(2).strip()
            elif current_param and stripped:
                # 续行追加
                result[current_param] += " " + stripped
    return result


def infer_parameters_from_signature(method: Any) -> dict[str, dict[str, Any]]:
    """从方法的签名中推断参数信息。

    使用 typing.get_type_hints() 解析被 PEP 563/649 转为字符串的类型注解。

    T10: 从 docstring Args 段填充 description；默认值写入描述尾注。

    Returns:
        {param_name: {"type": str, "required": bool, "default": Any, "description": str}}
    """
    sig = inspect.signature(method)
    try:
        hints = typing.get_type_hints(method)
    except Exception:
        hints = {}

    # 解析 docstring 参数描述
    doc = inspect.getdoc(method) or ""
    doc_args = _parse_docstring_args(doc)

    params: dict[str, dict[str, Any]] = {}
    for pname, param in sig.parameters.items():
        if pname in ("self", "kwargs", "args"):
            continue
        ann = hints.get(pname, param.annotation)
        json_type = annotation_to_json_type(ann)
        pinfo: dict[str, Any] = {
            "type": json_type,
            "required": param.default is inspect.Parameter.empty,
            "description": doc_args.get(pname, ""),
        }
        if param.default is not inspect.Parameter.empty and param.default is not None:
            pinfo["default"] = param.default
            if pinfo["description"]:
                pinfo["description"] += f" (默认: {param.default})"
        params[pname] = pinfo
    return params
