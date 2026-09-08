"""运行时配置存储 — 优先级 store > 环境变量/.env > 默认值。

落盘 ``anchor("settings.json")``（原子写）；secret 字段以
``{"__enc__": True, "v": <Fernet 密文>}`` 封装加密。无可用加密密钥时
**拒绝保存 secret 字段**（fail-closed），避免把明文密钥写进文件。

边界约定：
- 不回写 ``.env``（pydantic-settings 对未声明键抛 extra_forbidden，且
  明文密钥与密文同文件无意义）；
- 不替换 ``config.settings`` 单例（全库 183 处直接 import），只就地 setattr。

source 判定依赖模块导入时快照的 ``_ENV_KEYS``——必须在任何 ``apply_all``
``setattr`` 之前捕获，否则 pydantic v2 的 ``__setattr__`` 会把 store 键混入
``model_fields_set``。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from clipwright.config import Settings, logger, settings
from clipwright.paths import anchor
from clipwright.services import webhook_crypto

_STORE_FILE = "settings.json"
_ENC_PREFIX = "__enc__"

# 模块导入时快照：哪些字段由环境变量/.env 显式提供（见 docstring 的时序说明）
_ENV_KEYS: frozenset[str] = frozenset(getattr(settings, "model_fields_set", set()))

# 干净副本：env/.env 值（无 store 覆盖），供「清除覆盖」时还原
_PRISTINE: Settings = Settings()


def store_path() -> Path:
    return anchor(_STORE_FILE)


def _read_store() -> dict[str, Any]:
    p = store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("settings 存储读取失败，忽略: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _decrypt_entry(raw: Any) -> Any:
    if isinstance(raw, dict) and raw.get(_ENC_PREFIX):
        return webhook_crypto.decrypt_secret(str(raw.get("v", "")))
    return raw


def has_stored_overrides() -> bool:
    return bool(_read_store())


def load() -> dict[str, Any]:
    """读取全部 store 覆盖（secret 已解密）。"""
    return {k: _decrypt_entry(v) for k, v in _read_store().items()}


def source_of(key: str) -> str:
    """值来源：store（settings.json）> env（环境变量/.env）> default。"""
    if key in _read_store():
        return "store"
    if key in _ENV_KEYS:
        return "env"
    return "default"


def save(patch: dict[str, Any]) -> tuple[list[str], list[str]]:
    """写入覆盖。value 为 None/"" 表示清除该键的覆盖（回退 env/默认）。

    Returns:
        (applied_keys, cleared_keys)
    Raises:
        PermissionError: 尝试保存 secret 但未配置加密密钥。
    """
    from clipwright.config_schema import FIELD_META

    store = _read_store()
    applied: list[str] = []
    cleared: list[str] = []
    for key, value in patch.items():
        if key not in Settings.model_fields:
            raise KeyError(f"未知的配置字段: {key}")
        if value is None or value == "":
            store.pop(key, None)
            cleared.append(key)
            continue
        meta = FIELD_META.get(key)
        if meta is not None and meta.secret:
            if not webhook_crypto.has_key():
                raise PermissionError(
                    f"未配置加密密钥（CLIPWRIGHT_WEBHOOK_SECRET_KEY），"
                    f"拒绝明文保存 secret 字段 {key}"
                )
            store[key] = {
                _ENC_PREFIX: True,
                "v": webhook_crypto.encrypt_secret(str(value)),
            }
        else:
            store[key] = value
        applied.append(key)
    _write_store(store)
    return applied, cleared


def reset(keys: list[str] | None = None) -> list[str]:
    """清除覆盖（keys=None 清全部）；返回被清除的键。"""
    store = _read_store()
    if keys is None:
        cleared = list(store.keys())
        store.clear()
    else:
        cleared = [k for k in keys if k in store]
        for k in cleared:
            store.pop(k, None)
    _write_store(store)
    return cleared


def restore_to_settings(keys: list[str]) -> None:
    """把清除的键还原为 env/.env 值（无 env 值则回默认）。"""
    for key in keys:
        if key in Settings.model_fields:
            setattr(settings, key, getattr(_PRISTINE, key))


def apply_all(settings_obj: Settings | None = None) -> list[str]:
    """启动时把 store 覆盖就地写入 settings 单例；返回被覆盖的键。

    必须在 FastAPI app / 插件加载器 / 静态挂载构造之前调用，
    这些组件只在启动时读一次配置（CORS、uvicorn、Mongo 等）。
    """
    target = settings_obj if settings_obj is not None else settings
    applied: list[str] = []
    for key, value in load().items():
        if key not in Settings.model_fields:
            logger.warning("settings 存储含未知字段 %s，忽略", key)
            continue
        try:
            setattr(target, key, value)
            applied.append(key)
        except Exception as e:
            logger.warning("应用配置覆盖 %s 失败: %s", key, e)
    if applied:
        logger.info(
            "已从 %s 应用 %d 项配置覆盖: %s",
            _STORE_FILE, len(applied), ", ".join(sorted(applied)),
        )
    return applied
