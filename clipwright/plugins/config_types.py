"""插件配置类型系统 — 声明式字段定义与校验。

规范：插件 config.yaml 使用结构化 fields 格式：

    fields:
      api_key:
        type: string
        value: ""
        label: "API Key"
        description: "从 xxx 获取"

支持的 type: string | int | float | bool | dict | list
"""

from __future__ import annotations

from typing import Any

# 支持的类型枚举
TYPED_CONFIG_TYPES = frozenset({"string", "int", "float", "bool", "dict", "list"})

# type → Python 类型映射（用于校验）
_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "dict": dict,
    "list": list,
}


def typed_config_to_values(config: dict[str, Any]) -> dict[str, Any]:
    """从结构化配置中提取扁平键值对（向后兼容旧插件）。

    Args:
        config: 结构化配置 dict，含 "fields" 键

    Returns:
        {field_name: value} 扁平 dict，供插件通过 self.config["key"] 访问
    """
    fields = config.get("fields", {})
    if not isinstance(fields, dict):
        return {}
    return {
        name: field.get("value")
        for name, field in fields.items()
        if isinstance(field, dict) and "value" in field
    }


def validate_typed_config(config: dict[str, Any]) -> list[str]:
    """校验结构化配置，返回错误列表（空列表 = 通过）。

    Args:
        config: 待校验的配置 dict，应含 "fields" 键

    Returns:
        错误信息列表
    """
    errors: list[str] = []

    fields = config.get("fields")
    if not isinstance(fields, dict):
        errors.append("缺少 'fields' 键或格式错误，应为 dict")
        return errors

    for name, field in fields.items():
        if not isinstance(field, dict):
            errors.append(f"字段 '{name}' 的值应为 dict（含 type/value/label）")
            continue

        ft = field.get("type", "")
        if ft not in TYPED_CONFIG_TYPES:
            errors.append(
                f"字段 '{name}' 的 type='{ft}' 无效，支持: {', '.join(sorted(TYPED_CONFIG_TYPES))}"
            )
            continue

        if "value" not in field:
            errors.append(f"字段 '{name}' 缺少 'value'")
            continue

        val = field["value"]
        expected = _TYPE_MAP[ft]
        if not isinstance(val, expected):
            errors.append(
                f"字段 '{name}' value 类型应为 {ft}，实际为 {type(val).__name__}"
            )

    return errors
