"""Provider 源与角色绑定（B2）——「模型提供商」配置的持久化层。

设计（见 docs/provider-config-and-pluginization-remediation-plan.md §3）：
- **源与角色解耦**：ProviderSource 是凭据 + 端点 + 模型清单的实体；角色
  （main/flash/vision/embed/rerank/tts）只保存 (source_id, model) 指针，
  同一源可被多个角色复用。
- **加密落盘**：api_keys 走 webhook_crypto 的 Fernet（与插件 secret 同机制）。
- **向后兼容**：无任何源时，调用方回退既有 `settings.llm_*` / `rag_*` 平铺
  字段；`ensure_migrated()` 可把现有配置物化为一条默认源（不删旧字段）。
- **模型清单**：`models: [{id, name, enabled}]` 支持「获取模型列表 / 自定义 /
  启用开关 / 编辑 / 删除」；角色只能绑定 `enabled` 的模型。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.paths import anchor
from clipwright.services.webhook_crypto import decrypt_secret, encrypt_secret, has_key

#: 角色名单（与前端 tabs/角色徽标一致）
ROLES: tuple[str, ...] = ("main", "flash", "vision", "embed", "rerank", "tts")

#: 源的类型（对应前端顶部 tab）
SOURCE_KINDS: tuple[str, ...] = ("llm", "embedding", "rerank", "tts")

#: 角色 → 允许的源类型（P2：绑定前校验，避免把 TTS 源绑到 LLM 角色之后
#: 在运行期才报「未安装 LLM provider」这种难以定位的错误）
ROLE_EXPECTED_KINDS: dict[str, tuple[str, ...]] = {
    "main": ("llm",),
    "flash": ("llm",),
    "vision": ("llm",),
    "embed": ("embedding",),
    "rerank": ("rerank",),
    "tts": ("tts",),
}

_STORE_PATH: Optional[Path] = None
_LOCK = threading.RLock()


def store_path() -> Path:
    global _STORE_PATH
    if _STORE_PATH is None:
        _STORE_PATH = anchor("provider_sources.json")
    return _STORE_PATH


def _now() -> float:
    return time.time()


def _read() -> dict[str, Any]:
    p = store_path()
    if not p.exists():
        return {"sources": [], "roles": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 坏文件不阻断启动，备份后重置
        logger.warning("provider_sources.json 解析失败，已重置: %s", e)
        try:
            p.replace(p.with_suffix(".json.bad"))
        except Exception:
            pass
        return {"sources": [], "roles": {}}
    data.setdefault("sources", [])
    data.setdefault("roles", {})
    return data


def _write(data: dict[str, Any]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── 脱敏 ────────────────────────────────────────────────

def _mask_key(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}****" if len(value) > 4 else "****"


def _plain_keys(src: dict[str, Any]) -> list[str]:
    """源内密钥的明文本（供运行时使用/脱敏展示）。

    有加密密钥时一律尝试解密（`decrypt_secret` 对明文/损坏值原样返回）；
    不用 `is_encrypted` 形态启发式——Fernet 令牌的 padding 形态不稳定，
    误判会把密文当成 API Key 使用/回显（B3-fix）。
    """
    keys = [k for k in (src.get("api_keys") or []) if isinstance(k, str)]
    if not has_key():
        return keys
    return [decrypt_secret(k) for k in keys]


def _public_source(src: dict[str, Any]) -> dict[str, Any]:
    """对外形态：密钥只回显掩码，附 has_keys/key_count。"""
    out = {k: v for k, v in src.items() if k != "api_keys"}
    keys = _plain_keys(src)
    out["api_keys_masked"] = [_mask_key(k) for k in keys]
    out["key_count"] = len(keys)
    out["has_keys"] = bool(keys)
    return out


def _encrypt_incoming_keys(payload: dict[str, Any], existing: list[str]) -> list[str]:
    """入参 → 加密密钥列表。

    - `api_keys`: 明文列表（新值）；条目含 `****`（掩码回显）时按序保留旧值；
    - 未传 `api_keys` 键 → 保持旧值。
    """
    if "api_keys" not in payload:
        return list(existing)
    incoming = payload.get("api_keys")
    if not isinstance(incoming, list):
        return list(existing)
    out: list[str] = []
    for i, item in enumerate(incoming):
        if not isinstance(item, str) or not item:
            continue
        if "****" in item:  # 掩码回显 → 保留原位置的旧密钥
            if i < len(existing):
                out.append(existing[i])
            continue
        out.append(encrypt_secret(item))
    return out


# ── 源 CRUD ─────────────────────────────────────────────

def list_sources(kind: str = "", public: bool = True) -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_read()["sources"])
    if kind:
        items = [s for s in items if str(s.get("kind")) == kind]
    items.sort(key=lambda s: (str(s.get("kind")), str(s.get("name") or s.get("id"))))
    return [_public_source(s) for s in items] if public else items


def get_source(source_id: str, public: bool = True) -> Optional[dict[str, Any]]:
    with _LOCK:
        for s in _read()["sources"]:
            if s.get("id") == source_id:
                return _public_source(s) if public else dict(s)
    return None


def create_source(payload: dict[str, Any]) -> dict[str, Any]:
    """新建源。必填：name；kind ∈ SOURCE_KINDS；provider = 门面实现名。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("源名称不能为空")
    kind = str(payload.get("kind") or "llm")
    if kind not in SOURCE_KINDS:
        raise ValueError(f"未知的源类型: {kind}（可选: {', '.join(SOURCE_KINDS)}）")
    with _LOCK:
        data = _read()
        src = {
            "id": str(payload.get("id") or f"src_{uuid.uuid4().hex[:8]}"),
            "name": name,
            "kind": kind,
            "provider": str(payload.get("provider") or ""),
            "base_url": str(payload.get("base_url") or ""),
            "api_keys": _encrypt_incoming_keys(payload, []),
            "timeout_sec": float(payload.get("timeout_sec") or 120),
            "proxy": str(payload.get("proxy") or ""),
            "headers": dict(payload.get("headers") or {}),
            "models": _normalize_models(payload.get("models")),
            "created_at": _now(),
            "updated_at": _now(),
        }
        if any(s.get("id") == src["id"] for s in data["sources"]):
            raise ValueError(f"源 ID 已存在: {src['id']}")
        data["sources"].append(src)
        _write(data)
    logger.info("Provider 源已创建: %s(%s)", src["id"], kind)
    return _public_source(src)


def update_source(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        for src in data["sources"]:
            if src.get("id") != source_id:
                continue
            if "kind" in payload and str(payload["kind"]) not in SOURCE_KINDS:
                raise ValueError(f"未知的源类型: {payload['kind']}")
            # P2：改 kind 会让已绑定的角色错配（如 llm→tts 后 main 指向 TTS 源）
            new_kind = str(payload.get("kind") or "")
            if new_kind and new_kind != str(src.get("kind") or ""):
                bound = [
                    role for role, binding in (data.get("roles") or {}).items()
                    if isinstance(binding, dict) and binding.get("source_id") == source_id
                ]
                if bound:
                    raise ValueError(
                        f"源仍被角色绑定（{', '.join(sorted(bound))}），不能改类型；"
                        "请先解绑这些角色"
                    )
            for key in ("name", "kind", "provider", "base_url", "proxy"):
                if key in payload and payload[key] is not None:
                    src[key] = str(payload[key])
            if "timeout_sec" in payload and payload["timeout_sec"] is not None:
                src["timeout_sec"] = float(payload["timeout_sec"])
            if "headers" in payload and isinstance(payload["headers"], dict):
                src["headers"] = dict(payload["headers"])
            src["api_keys"] = _encrypt_incoming_keys(payload, list(src.get("api_keys") or []))
            if "models" in payload:
                src["models"] = _normalize_models(payload.get("models"))
            src["updated_at"] = _now()
            unbound = _unbind_stale_roles(data, source_id)  # P3
            _write(data)
            out = _public_source(src)
            out["unbound_roles"] = unbound
            return out
    raise FileNotFoundError(f"源不存在: {source_id}")


def delete_source(source_id: str) -> dict[str, Any]:
    """删除源，并解绑引用它的角色（避免悬空绑定）。"""
    with _LOCK:
        data = _read()
        before = len(data["sources"])
        data["sources"] = [s for s in data["sources"] if s.get("id") != source_id]
        if len(data["sources"]) == before:
            raise FileNotFoundError(f"源不存在: {source_id}")
        unbound = [r for r, b in list(data["roles"].items())
                   if isinstance(b, dict) and b.get("source_id") == source_id]
        for r in unbound:
            data["roles"].pop(r, None)
        _write(data)
    logger.info("Provider 源已删除: %s（解绑角色: %s）", source_id, unbound)
    return {"status": "deleted", "id": source_id, "unbound_roles": unbound}


# ── 模型清单 ────────────────────────────────────────────

def _normalize_models(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in raw or []:
        if isinstance(m, str):
            mid, name = m, m
            enabled = True
        elif isinstance(m, dict):
            mid = str(m.get("id") or "").strip()
            name = str(m.get("name") or mid)
            enabled = bool(m.get("enabled", True))
        else:
            continue
        if not mid:
            continue
        out.append({"id": mid, "name": name, "enabled": enabled})
    # 去重（保留首个）
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for m in out:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        uniq.append(m)
    return uniq


def set_models(source_id: str, models: Any) -> dict[str, Any]:
    return update_source(source_id, {"models": models})


def upsert_model(source_id: str, model: dict[str, Any]) -> dict[str, Any]:
    """「自定义模型」：新增或按 id 覆盖。"""
    mid = str(model.get("id") or "").strip()
    if not mid:
        raise ValueError("模型 id 不能为空")
    with _LOCK:
        src = get_source(source_id, public=False)
        if src is None:
            raise FileNotFoundError(f"源不存在: {source_id}")
        models = _normalize_models(src.get("models"))
        entry = {"id": mid, "name": str(model.get("name") or mid),
                 "enabled": bool(model.get("enabled", True))}
        for i, m in enumerate(models):
            if m["id"] == mid:
                models[i] = {**m, **entry}
                break
        else:
            models.append(entry)
        return set_models(source_id, models)


def delete_model(source_id: str, model_id: str) -> dict[str, Any]:
    """删除模型；引用它的角色自动解绑（P3：统一走 _unbind_stale_roles）。"""
    with _LOCK:
        src = get_source(source_id, public=False)
        if src is None:
            raise FileNotFoundError(f"源不存在: {source_id}")
        models = [m for m in _normalize_models(src.get("models")) if m["id"] != model_id]
    result = set_models(source_id, models)
    if isinstance(result, dict):
        result.setdefault("unbound_roles", [])
    return result


# ── 角色绑定 ────────────────────────────────────────────

def get_roles() -> dict[str, Any]:
    with _LOCK:
        return dict(_read().get("roles") or {})


def set_role(role: str, source_id: Optional[str], model: str = "") -> dict[str, Any]:
    """把角色绑定到 (源, 模型)；source_id 为空则解绑（回退旧平铺配置）。

    P2：校验「角色 ↔ 源类型」——把 TTS 源绑到 `main`/`flash`/`vision` 这类错配
    会在运行期才炸（报「未安装 LLM provider: qwen-tts」），这里提前拒绝并说明。
    """
    if role not in ROLES:
        raise ValueError(f"未知角色: {role}（可选: {', '.join(ROLES)}）")
    with _LOCK:
        data = _read()
        if not source_id:
            data.get("roles", {}).pop(role, None)
            _write(data)
            return {"role": role, "bound": False}
        src = next((s for s in data["sources"] if s.get("id") == source_id), None)
        if src is None:
            raise FileNotFoundError(f"源不存在: {source_id}")
        expected = ROLE_EXPECTED_KINDS.get(role, ())
        kind = str(src.get("kind") or "")
        if expected and kind not in expected:
            raise ValueError(
                f"角色 {role} 需要 {'/'.join(expected)} 类型的源，"
                f"而源 {src.get('name') or source_id} 的类型是 {kind or '未设置'}"
            )
        model = str(model or "").strip()
        models = _normalize_models(src.get("models"))
        if model:
            hit = next((m for m in models if m["id"] == model), None)
            if hit is None:
                raise ValueError(f"模型不在源的清单中: {model}")
            if not hit.get("enabled", True):
                raise ValueError(f"模型已停用，无法绑定: {model}")
        data.setdefault("roles", {})[role] = {
            "source_id": source_id, "model": model, "updated_at": _now(),
        }
        _write(data)
    return {"role": role, "bound": True, "source_id": source_id, "model": model}


def _unbind_stale_roles(data: dict[str, Any], source_id: str) -> list[str]:
    """P3：源内模型被删/被停用后，解绑引用它的角色（返回被解绑的角色名）。

    绑定时的「模型必须在清单里且启用」校验，此前只在绑的那一刻生效；
    绑定后把模型停用，`resolve()` 会继续用它——语义自相矛盾，且用户以为已失效。
    """
    src = next((s for s in data["sources"] if s.get("id") == source_id), None)
    if src is None:
        return []
    usable = {
        str(m.get("id")) for m in _normalize_models(src.get("models"))
        if m.get("enabled", True)
    }
    unbound: list[str] = []
    for role, binding in list((data.get("roles") or {}).items()):
        if not isinstance(binding, dict) or binding.get("source_id") != source_id:
            continue
        if str(binding.get("model") or "") not in usable:
            data["roles"].pop(role, None)
            unbound.append(role)
    return unbound


# ── 运行时解析 ──────────────────────────────────────────

def resolve(role: str) -> Optional[dict[str, Any]]:
    """按角色解析运行时凭据（明文）。

    返回 ``{provider, api_key, base_url, model, timeout_sec, proxy, headers,
    source_id, source_name}``；未绑定或源缺失时返回 None（调用方回退旧字段）。
    """
    if role not in ROLES:
        return None
    with _LOCK:
        data = _read()
        binding = (data.get("roles") or {}).get(role)
        if not isinstance(binding, dict):
            return None
        src = next((s for s in data["sources"] if s.get("id") == binding.get("source_id")), None)
        if src is None:
            return None
        keys = [k for k in _plain_keys(src) if k]
        model = str(binding.get("model") or "")
        if not model:
            models = [m for m in _normalize_models(src.get("models")) if m.get("enabled", True)]
            model = models[0]["id"] if models else ""
    return {
        "provider": str(src.get("provider") or ""),
        "api_key": keys[0] if keys else None,
        "base_url": str(src.get("base_url") or "") or None,
        "model": model,
        "timeout_sec": float(src.get("timeout_sec") or 120),
        "proxy": str(src.get("proxy") or "") or None,
        "headers": dict(src.get("headers") or {}),
        "source_id": src.get("id"),
        "source_name": src.get("name"),
    }


def resolve_for_source(source_id: str) -> Optional[dict[str, Any]]:
    """按源 id 解析凭据（不看角色）——供「获取模型列表」等源级操作用。"""
    with _LOCK:
        src = get_source(source_id, public=False)
        if src is None:
            return None
        keys = [k for k in _plain_keys(src) if k]
    return {
        "provider": str(src.get("provider") or ""),
        "api_key": keys[0] if keys else None,
        "base_url": str(src.get("base_url") or "") or None,
        "timeout_sec": float(src.get("timeout_sec") or 120),
        "proxy": str(src.get("proxy") or "") or None,
        "headers": dict(src.get("headers") or {}),
        "source_id": src.get("id"),
        "source_name": src.get("name"),
    }


# ── 迁移（现有平铺配置 → 一条默认源）──────────────────────

def ensure_migrated() -> Optional[str]:
    """若尚无任何源，且旧平铺配置有凭据 → 物化一条默认源并绑定 LLM 角色。

    幂等：已有源则直接返回 None（不覆盖用户配置）。
    """
    from clipwright.config import settings

    with _LOCK:
        if _read()["sources"]:
            return None
    api_key = str(getattr(settings, "llm_api_key", "") or "")
    base_url = str(getattr(settings, "llm_base_url", "") or "")
    model = str(getattr(settings, "llm_model", "") or "")
    provider = str(getattr(settings, "llm_provider", "") or "")
    if not (api_key or base_url):
        return None  # 没配过 → 无需迁移
    src = create_source({
        "id": "default",
        "name": f"默认（{provider or 'llm'}）",
        "kind": "llm",
        "provider": provider,
        "base_url": base_url,
        "api_keys": [api_key] if api_key else [],
        "timeout_sec": 120,
        "models": [model] if model else [],
    })
    for role in ("main", "flash", "vision"):
        try:
            set_role(role, src["id"], model)
        except Exception as e:  # noqa: BLE001 — 迁移不因单角色失败而中断
            logger.warning("迁移绑定角色 %s 失败: %s", role, e)
    logger.info("已从现有配置迁移出默认 Provider 源: %s", src["id"])
    return str(src["id"])


def reset_for_tests(path: Optional[Path] = None) -> None:
    """测试用：重定向存储路径并清空。"""
    global _STORE_PATH
    with _LOCK:
        _STORE_PATH = path
