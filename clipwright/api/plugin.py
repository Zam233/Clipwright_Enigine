"""插件 API — 第三方插件的发现、加载、管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from clipwright.plugins import PluginLoadError
from clipwright.schema.plugin import PluginMetadata

# PluginLoader 在 main.py lifespan 中注入
_loader: "PluginLoader | None" = None  # noqa: F821


def set_loader(loader: "PluginLoader") -> None:  # noqa: F821
    global _loader
    _loader = loader


router = APIRouter(prefix="/api/plugin", tags=["plugin"])


def _require_admin(request: Request) -> None:
    """P3: jwt 模式下插件写操作需 admin 角色；off/token 模式放行。"""
    from clipwright.authz import current_user_id, is_admin
    uid = current_user_id(request)
    if uid is not None and not is_admin(request):
        raise HTTPException(status_code=403, detail="插件管理操作需要管理员权限")


def _validate_plugin_id(plugin_id: str) -> str:
    """审计 P1 修复：所有含 plugin_id 的端点先校验 ID 合法性，
    防止 ../ 路径穿越（如 GET /{plugin_id}/ui 直接拼接文件路径）。"""
    from clipwright.security import SecurityViolation, validate_id
    try:
        return validate_id(plugin_id, "plugin_id")
    except SecurityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/list", response_model=list[PluginMetadata])
async def list_plugins() -> list[PluginMetadata]:
    """列出所有已加载的第三方插件。"""
    if _loader is None:
        return []
    return _loader.list_loaded()


@router.get("/discover", response_model=list[str])
async def discover_plugins() -> list[str]:
    """发现插件目录中的所有可用插件（不加载）。"""
    if _loader is None:
        return []
    return _loader.discover()


@router.post("/load/{plugin_id}", response_model=PluginMetadata)
async def load_plugin(plugin_id: str, request: Request = None) -> PluginMetadata:
    """加载并初始化指定插件。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    try:
        plugin = _loader.load(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
        meta = _loader.list_loaded()
        for m in meta:
            if m.manifest.id == plugin_id:
                return m
        raise HTTPException(status_code=500, detail="Plugin loaded but metadata missing")
    except PluginLoadError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/unload/{plugin_id}")
async def unload_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """卸载指定插件。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.unload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """M8: 启用插件（持久化 + 加载）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    # P1-12: set_enabled 也要进 try——它内部对未加载插件会触发 load()，签名/
    # 依赖不满足时抛 PluginLoadError，落在 try 外会导致状态文件已写 enabled
    # 而回滚（set_enabled(False)）永不执行 → 重启后「已启用但未加载」
    try:
        _loader.set_enabled(plugin_id, True)
        _loader.load(plugin_id)
    except Exception as e:
        # 加载失败回退禁用态并返回 502（旧实现裸 500 且状态不一致）
        _loader.set_enabled(plugin_id, False)
        raise HTTPException(status_code=502, detail=f"插件加载失败: {str(e)[:200]}")
    return {"status": "ok", "plugin_id": plugin_id, "enabled": "true"}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """M8: 禁用插件（持久化 + 卸载）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    _loader.set_enabled(plugin_id, False)
    # 禁用时同时注销 Hook + Prompt（能力即时收缩）
    try:
        from clipwright.plugins.hooks import HookRegistry
        HookRegistry.unregister_plugin(plugin_id)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("禁用插件 Hook 清理失败 %s: %s", plugin_id, e)
    try:
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        PluginPromptRegistry.unregister(plugin_id)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("禁用插件 Prompt 清理失败 %s: %s", plugin_id, e)
    # SA-2: 禁用时同步注销插件 Agent（能力即时收缩）
    try:
        from clipwright.agents.registry import AgentRegistry
        AgentRegistry.unregister_plugin(plugin_id)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("禁用插件 Agent 清理失败 %s: %s", plugin_id, e)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": "false"}


@router.get("/permissions")
async def list_permissions() -> dict[str, object]:
    """M1: 已知权限白名单（供前端安装确认展示）。"""
    from clipwright.config import settings
    return {"allowed": settings.plugin_allowed_permissions}


# ── M7: 插件错误通道 ──


@router.get("/errors")
async def list_plugin_errors(limit: int = 50, request: Request = None) -> list[dict]:
    """M7: 插件错误通道 — 最近错误列表（诊断用）。P3-09: admin 门禁。"""
    _require_admin(request)
    from clipwright.plugins.error_bus import get_error_bus
    return get_error_bus().list(limit=max(1, min(limit, 200)))


@router.get("/contributions")
async def list_contributions(
    kind: str = "",
    slot: str = "",
    owner: str = "",
    request: Request = None,
) -> dict[str, object]:
    """A-6/D-1：列出已注册贡献（可按 kind / ui_slot 的 slot 名 / owner 过滤）。

    D 的关键数据源：前端只调这一个端点即可拿到全部插件在某 slot 的贡献
    （不必逐个插件拉取）。owner 过滤用于「谁提供了这个工具」的溯源。
    """
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    filter_kind = None
    if kind:
        try:
            filter_kind = ContributionKind(kind)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"未知贡献类型: {kind}")

    out: list[dict[str, object]] = []
    for owner_id, item in ContributionRegistry.list(filter_kind):
        if owner and owner_id != owner:
            continue
        if slot and str(item.meta.get("slot", "")) != slot:
            continue
        meta = dict(item.meta)
        entry: dict[str, object] = {
            "kind": item.kind.value,
            "id": item.id,
            "owner": owner_id,
            "meta": meta,
        }
        # 所有 kind 统一透传前端消费的顶层字段（export_preset/shortcut/
        # timeline.tool 等非 ui_slot 类同样需要 payload/icon/visibleWhen）
        for key in ("title", "order", "icon", "visibleWhen", "payload"):
            if key in meta:
                entry[key] = meta[key]
        if item.kind is ContributionKind.UI_SLOT:
            # D-2 渲染器直接消费 SlotContribution 形状（slot/title/order/layout…）
            entry.update({
                "slot": str(meta.get("slot", "")),
                "title": str(meta.get("title") or item.id),
                "order": meta.get("order", 100),
                **({"icon": meta["icon"]} if meta.get("icon") else {}),
                **({"visibleWhen": meta["visibleWhen"]} if meta.get("visibleWhen") else {}),
                **({"layout": meta["layout"]} if meta.get("layout") else {}),
                **({"payload": meta["payload"]} if meta.get("payload") else {}),
            })
        out.append(entry)

    # D-1 兼容层：旧 ui.json（{title, widgets}）→ settings.section slot
    # （仅在查询该 slot 或未按 slot 过滤时扫描；插件的 ui.json 不进门面，
    #  由本端点按需包装——30 个内置插件一行不改即可被前端 Slot 渲染器消费）
    if (not slot or slot == "settings.section") and _loader is not None:
        seen = {str(e.get("id")) for e in out}
        try:
            discovered = _loader.discover()
        except Exception:
            discovered = []
        for pid in discovered:
            if owner and pid != owner:
                continue
            ui_file = _loader.plugin_dir / pid / "ui.json"
            if not ui_file.exists():
                continue
            try:
                import json as _json
                raw = _json.loads(ui_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict) or "slots" in raw or "widgets" not in raw:
                continue  # 新格式或非布局文件
            entry_id = f"{pid}_legacy"
            if entry_id in seen:
                continue
            title = str(raw.get("title") or pid)
            out.append({
                "kind": "ui_slot",
                "id": entry_id,
                "owner": pid,
                "slot": "settings.section",
                "title": title,
                "order": 200,
                "layout": {"title": title, "widgets": raw.get("widgets") or []},
                "legacy": True,
                "meta": {"slot": "settings.section"},
            })
    return {"contributions": out, "total": len(out)}


@router.get("/health")
async def plugin_health() -> dict[str, object]:
    """P10: 插件健康聚合视图 — 单端点总览加载/签名/依赖/错误/Hook 注册状态。

    overall: ok（无异常插件）/ degraded（有非致命问题）/ error（有加载失败类错误）。
    """
    from clipwright.agents.registry import AgentRegistry
    from clipwright.plugins.error_bus import get_error_bus
    from clipwright.plugins.hooks import HookPoint, HookRegistry
    from clipwright.plugins.prompt_registry import PluginPromptRegistry

    if _loader is None:
        return {"overall": "ok", "plugins": [], "errors_total": 0}

    errors = get_error_bus().list(limit=200)
    err_count: dict[str, int] = {}
    for e in errors:
        err_count[e.get("plugin_id", "")] = err_count.get(e.get("plugin_id", ""), 0) + 1

    hook_count: dict[str, int] = {}
    for hp in HookPoint:
        for fn in HookRegistry._hooks.get(hp, []):
            pid = getattr(fn, "__plugin_id__", None)
            if pid:
                hook_count[pid] = hook_count.get(pid, 0) + 1
    # SA-2: 每插件 Agent 计数
    agent_count: dict[str, int] = {}
    for e in AgentRegistry.list_all():
        agent_count[e.plugin_id] = agent_count.get(e.plugin_id, 0) + 1

    plugins: list[dict[str, object]] = []
    discovered = set(_loader.discover())
    for m in _loader.list_loaded():
        pid = m.manifest.id
        issues: list[str] = []
        if err_count.get(pid):
            issues.append(f"{err_count[pid]} 条错误")
        if not m.enabled:
            issues.append("已禁用")
        if m.signed and not m.verified:
            issues.append("签名校验失败")
            plugins.append({
                "plugin_id": pid, "name": m.manifest.name, "version": m.manifest.version,
                "status": "error", "issues": issues,
                "enabled": m.enabled, "signed": m.signed, "verified": m.verified,
                "dependency_ok": m.dependency_ok, "missing_dependencies": m.missing_dependencies,
                "hooks": hook_count.get(pid, 0),
                "agents": agent_count.get(pid, 0),
            })
            continue
        if not m.dependency_ok:
            issues.append(f"缺依赖 {m.missing_dependencies}")
        status = "degraded" if issues else "ok"
        plugins.append({
            "plugin_id": pid, "name": m.manifest.name, "version": m.manifest.version,
            "status": status, "issues": issues,
            "enabled": m.enabled, "signed": m.signed, "verified": m.verified,
            "dependency_ok": m.dependency_ok, "missing_dependencies": m.missing_dependencies,
            "hooks": hook_count.get(pid, 0),
            "agents": agent_count.get(pid, 0),
        })

    # 已发现但未加载的插件（含被禁用/加载失败残留）
    loaded_ids = {p["plugin_id"] for p in plugins}
    for pid in sorted(discovered - loaded_ids):
        issues = ["未加载"]
        if err_count.get(pid):
            issues.append(f"{err_count[pid]} 条错误")
        plugins.append({
            "plugin_id": pid, "name": pid, "version": "",
            "status": "degraded", "issues": issues,
            "enabled": _loader.is_enabled(pid),
            "signed": None, "verified": None,
            "dependency_ok": None, "missing_dependencies": [],
            "hooks": hook_count.get(pid, 0),
            "agents": agent_count.get(pid, 0),
        })

    statuses = {p["status"] for p in plugins}
    overall = "error" if "error" in statuses else ("degraded" if "degraded" in statuses else "ok")
    return {
        "overall": overall,
        "plugins": plugins,
        "errors_total": len(errors),
        "prompts_registered": sum(
            len(v) for v in PluginPromptRegistry.list_registered().values()),
    }


@router.delete("/errors")
async def clear_plugin_errors(plugin_id: str = "", request: Request = None) -> dict[str, object]:
    """M7: 清空插件错误通道（可按插件过滤）。P3-09: admin 门禁。"""
    _require_admin(request)
    from clipwright.plugins.error_bus import get_error_bus
    removed = get_error_bus().clear(plugin_id or None)
    return {"status": "ok", "removed": removed}


@router.delete("/{plugin_id}")
async def unregister_plugin(plugin_id: str, request: Request = None) -> dict[str, str]:
    """P1-1: 注销插件 — 卸载 + 清理 PluginData（含 hook 与配置）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if _loader.get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not loaded")
    _loader.unload(plugin_id)
    # 清理 PluginData 目录（config/数据）
    try:
        data_dir = _loader.get_plugin_data_dir(plugin_id, ensure=False)
        import shutil
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("插件数据目录清理失败 %s: %s", plugin_id, e)
    # 统一注销 Hook + Prompt
    try:
        from clipwright.plugins.hooks import HookRegistry
        HookRegistry.unregister_plugin(plugin_id)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("插件 Hook 清理失败 %s: %s", plugin_id, e)
    try:
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        PluginPromptRegistry.unregister(plugin_id)
    except Exception as e:
        from clipwright.config import logger
        logger.warning("插件 Prompt 清理失败 %s: %s", plugin_id, e)
    return {"status": "unregistered", "plugin_id": plugin_id}


# ── 插件配置管理 ──

@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, object]:
    """读取插件结构化配置（含 type/value/label 元数据）。

    M13: 已发现但未加载的插件同样返回（源码默认 + PluginData 覆盖），
    支持预配置；完全不存在（未发现）的插件返回 404。
    """
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    # P2-50: get_typed_config 的掩码算在密文上（未先解密）——此处解密后
    # 重新掩码，回显形态恢复为「明文前 4 字符 + ****」
    from clipwright.plugins.config_types import decrypt_field_value, mask_secret_value
    config = _loader.get_typed_config(plugin_id)
    for fdef in (config.get("fields") or {}).values():
        if isinstance(fdef, dict) and fdef.get("secret") is True:
            try:
                plain = decrypt_field_value(fdef, fdef.get("value"))
                if isinstance(plain, str) and plain:
                    fdef["value"] = mask_secret_value(plain)
            except Exception:
                continue
    return config


@router.put("/{plugin_id}/config")
async def put_plugin_config(plugin_id: str, request: Request) -> dict[str, object]:
    """写入插件配置（YAML 格式），校验类型后存入 PluginData。

    M13: 已发现但未加载的插件同样可保存（预配置），加载后生效。
    """
    _require_admin(request)  # 配置可含 secret 凭据，jwt 模式下仅管理员可写
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    import yaml

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Config must be a YAML mapping")

    # 校验结构化配置
    from clipwright.plugins.config_types import validate_typed_config
    errors = validate_typed_config(data)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "配置校验失败", "errors": errors})

    # secret 掩码回写防护——如果 PUT 值包含 ****（掩码标记），保留原加密值
    # secret 掩码回写防护——PUT 值含 ****（掩码标记）时保留旧值。
    # P1-10/P2-51: 旧守卫只对「已加载插件」生效（读实例 config）——未加载插件
    # 的预配置保存会把掩码当真值落盘。现统一从 merged 配置解密取旧值，
    # 未配置过（无旧值）时置空串而非保留掩码。
    from clipwright.plugins.loader import _decrypt_flat
    try:
        _current_plain = _decrypt_flat(_loader._get_merged_config(plugin_id))
    except Exception:
        _current_plain = {}
    for fname, fdef in (data.get("fields") or {}).items():
        if isinstance(fdef, dict) and isinstance(fdef.get("value"), str) and "****" in fdef["value"]:
            orig_val = _current_plain.get(fname)
            fdef["value"] = orig_val if isinstance(orig_val, str) and orig_val else ""
    _loader.save_config(plugin_id, data)
    _loader.reload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.delete("/{plugin_id}/config")
async def delete_plugin_config(plugin_id: str, request: Request) -> dict[str, str]:
    """删除数据目录的 config.yaml，回退到源码默认配置。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    config_path = _loader.get_plugin_data_dir(plugin_id) / "config.yaml"

    if config_path.exists():
        config_path.unlink()

    # 刷新并重载
    plugin = _loader.get(plugin_id)
    if plugin:
        from clipwright.plugins.loader import _extract_flat_values
        plugin.config = _extract_flat_values(_loader._get_merged_config(plugin_id))

    _loader.reload(plugin_id)
    return {"status": "ok", "plugin_id": plugin_id}


@router.post("/load-all", response_model=list[str])
async def load_all_plugins() -> list[str]:
    """发现并加载所有可用插件。"""
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    return _loader.load_all()


@router.get("/capabilities")
async def get_capabilities() -> dict:
    """获取系统全部能力概览（插件 + 工具 + 技能 + 素材源 + 插件 Agent）。"""
    from clipwright.agents.registry import AgentRegistry
    from clipwright.material import MaterialRegistry
    from clipwright.skill import SkillRegistry
    from clipwright.tool import ToolRegistry

    return {
        "tools": ToolRegistry.list(),
        "skills": SkillRegistry.list(),
        "material_sources": MaterialRegistry.list(),
        "plugins": _loader.list_loaded() if _loader else [],
        # SA-2: 插件 Agent 清单（名/插件/依赖/说明）
        "agents": [
            {
                "name": e.name,
                "plugin_id": e.plugin_id,
                "deps": e.deps,
                "description": e.description,
            }
            for e in AgentRegistry.list_all()
        ],
    }


# ── 插件前端 UI ──


@router.get("/{plugin_id}/ui")
async def get_plugin_ui(plugin_id: str) -> dict:
    """返回插件的 UI 布局定义（ui.json）。

    D-1 双格式兼容（方案 §6）：
    - 新格式（含 ``slots`` 键）→ 原样返回；
    - 旧格式（``{title, widgets}``）→ 包成单个 ``settings.section`` slot 返回，
      并附 ``"legacy": true``——30 个内置插件的 ui.json 一行不改即可继续工作。
    """
    _validate_plugin_id(plugin_id)  # 审计 P1 修复：阻断 ../ 路径穿越
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    ui_file = _loader.plugin_dir / plugin_id / "ui.json"
    if not ui_file.exists():
        return {"widgets": []}
    try:
        import json as _json
        raw = _json.loads(ui_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read plugin UI: {e}")

    if isinstance(raw, dict) and "slots" in raw:
        return raw  # 新格式原样
    if isinstance(raw, dict) and "widgets" in raw:
        # 旧格式 → settings.section slot（前端 Slot 渲染器统一消费）
        title = str(raw.get("title") or "插件设置")
        return {
            "api_version": "2",
            "legacy": True,
            "slots": [{
                "slot": "settings.section",
                "id": f"{plugin_id}_legacy",
                "title": title,
                "layout": {"title": title, "widgets": raw.get("widgets") or []},
            }],
        }
    return raw
