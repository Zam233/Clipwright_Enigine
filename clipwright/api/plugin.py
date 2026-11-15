"""插件 API — 第三方插件的发现、加载、管理。"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clipwright.config import logger
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


# ── B5/B6：贡献读取路径的缓存 ──────────────────────────────
# `/contributions` 是设置页首屏的数据源，此前每请求都要：①扫盘 discover()
# （O(插件数) 次 fs 访问）②读每个 ui.json；且启动后新注册的内置 hook/prompt
# 不在门面里（镜像只在 lifespan 跑过一次）。这里加两层短 TTL 缓存。
_PLUGIN_SCAN_TTL_SEC = 10.0
_UI_FILE_TTL_SEC = 10.0
_MIRROR_TTL_SEC = 10.0
_scan_cache: dict[str, object] = {"at": 0.0, "ids": []}
_ui_cache: dict[str, tuple[float, dict | None]] = {}
_mirror_at: dict[str, float] = {"at": 0.0}


def _now() -> float:
    import time
    return time.time()


def _invalidate_plugin_caches() -> None:
    """插件启停/重载后调用：立刻失效扫盘与 ui.json 缓存。"""
    _scan_cache["at"] = 0.0
    _scan_cache["ids"] = []
    _ui_cache.clear()


def _discover_plugins_cached() -> list[str]:
    if _loader is None:
        return []
    if _now() - float(_scan_cache["at"]) < _PLUGIN_SCAN_TTL_SEC:  # type: ignore[arg-type]
        return list(_scan_cache["ids"])  # type: ignore[arg-type]
    try:
        discovered = list(_loader.discover())
    except Exception:  # noqa: BLE001 — 发现失败不应让贡献列表 500
        discovered = []
    _scan_cache["at"] = _now()
    _scan_cache["ids"] = discovered
    return discovered


def _legacy_ui_file(plugin_id: str) -> dict | None:
    """读插件 ui.json（旧格式才返回；带 mtime 失效的缓存）。"""
    if _loader is None:
        return None
    ui_file = _loader.plugin_dir / plugin_id / "ui.json"
    try:
        mtime = ui_file.stat().st_mtime
    except OSError:
        return None
    cached = _ui_cache.get(plugin_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    raw: dict | None = None
    try:
        import json as _json
        data = _json.loads(ui_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "slots" not in data and "widgets" in data:
            raw = data
    except Exception:  # noqa: BLE001 — 坏文件按「无 UI」处理
        raw = None
    _ui_cache[plugin_id] = (mtime, raw)
    return raw


def _maybe_refresh_builtin_mirror() -> None:
    """B6：按 TTL 重扫内置贡献镜像（启动后新注册的 hook/prompt 也能入账）。"""
    if _now() - _mirror_at["at"] < _MIRROR_TTL_SEC:
        return
    _mirror_at["at"] = _now()
    try:
        from clipwright.plugins.builtin_contributions import ingest_builtin_contributions
        ingest_builtin_contributions()
    except Exception as e:  # noqa: BLE001 — 读路径不能因镜像失败而失败
        from clipwright.config import logger
        logger.warning("内置贡献镜像重扫失败: %s", e)


def _validate_plugin_id(plugin_id: str) -> str:
    """审计 P1 修复：所有含 plugin_id 的端点先校验 ID 合法性，
    防止 ../ 路径穿越（如 GET /{plugin_id}/ui 直接拼接文件路径）。"""
    from clipwright.security import SecurityViolation, validate_id
    try:
        return validate_id(plugin_id, "plugin_id")
    except SecurityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))


def _attach_contributions(items: list) -> None:
    """P12：为每个插件补 `contributions` / `declared_only`（管理面溯源用）。

    数据源是门面账本（双轨都会入账），因此「界面显示已加载但实际零贡献」
    这类问题可以直接看出来。
    """
    from clipwright.plugins.contribution import ContributionRegistry

    for meta in items:
        pid = str(getattr(meta.manifest, "id", "") or "")
        registered = ContributionRegistry.list_by_owner(pid)
        meta.contributions = [{"kind": c.kind.value, "id": c.id} for c in registered]
        registered_keys = {f"{c.kind.value}:{c.id}" for c in registered}
        declared = {
            f"{str(getattr(d, 'kind', '') or '')}:{str(getattr(d, 'id', '') or '')}"
            for d in list(getattr(meta.manifest, "contributions", []) or [])
        }
        meta.declared_only = sorted(k for k in declared - registered_keys if k != ":")


@router.get("/list", response_model=list[PluginMetadata])
async def list_plugins() -> list[PluginMetadata]:
    """列出所有已加载的第三方插件（P12：附已注册贡献与「声明未注册」清单）。"""
    if _loader is None:
        return []
    items = _loader.list_loaded()
    _attach_contributions(items)
    return items


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
        _invalidate_plugin_caches()
        raise HTTPException(status_code=502, detail=f"插件加载失败: {str(e)[:200]}")
    _invalidate_plugin_caches()
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
    _invalidate_plugin_caches()
    return {"status": "ok", "plugin_id": plugin_id, "enabled": "false"}


class RuntimeRegistration(BaseModel):
    """管理员登记一个不透明运行时（M3）：engine 只认 id/可执行文件/摘要。"""

    runtime_id: str
    executable: str
    version: str = ""
    digest: str = ""
    approved: bool = False


@router.get("/runtimes")
async def list_runtimes(request: Request = None) -> dict:
    """列出已登记 runtime（不暴露 executable 绝对路径）。"""
    _require_admin(request)
    from clipwright.services.plugin_rpc import runtime_registry

    return {"runtimes": runtime_registry().list()}


@router.post("/runtimes")
async def register_runtime_api(
    body: RuntimeRegistration, request: Request = None,
) -> dict:
    """登记/更新 runtime；批准状态由调用方显式给出（生产应走审批流程）。"""
    _require_admin(request)
    from clipwright.services.plugin_jobs import RuntimeSpec
    from clipwright.services.plugin_rpc import register_runtime

    spec = register_runtime(RuntimeSpec(
        runtime_id=body.runtime_id,
        executable=body.executable,
        version=body.version,
        digest=body.digest,
        approved=body.approved,
    ))
    return {
        "runtime_id": spec.runtime_id,
        "version": spec.version,
        "approved": spec.approved,
    }


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


@router.get("/{plugin_id}/bundle")
async def get_plugin_bundle(plugin_id: str, request: Request = None):
    """M8：受信任 UI bundle（iframe 沙箱档）——默认关闭，且必须带签名。

    门禁（fail-closed，全部在端点层校验，不依赖加载器）：
    1. `settings.plugin_allow_ui_bundle` 未开启 → 403；
    2. 插件未发现或缺少 `bundle.html` → 404；
    3. 插件 manifest 无 `signature` → 403（受信任档要求签名，与 Gate G-2 一致）。

    返回 HTML 时附带严格 CSP；前端以 `<iframe sandbox="allow-scripts">` 加载，
    通过 postMessage 与宿主通信（禁止直接 import 插件 JS 到宿主上下文）。
    """
    from clipwright.config import settings
    from fastapi.responses import HTMLResponse

    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if not bool(getattr(settings, "plugin_allow_ui_bundle", False)):
        raise HTTPException(
            status_code=403,
            detail="插件 UI bundle 档未开启（设置 plugin_allow_ui_bundle=true 后可用）",
        )
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    bundle_file = _loader.plugin_dir / plugin_id / "bundle.html"
    if not bundle_file.exists():
        raise HTTPException(status_code=404, detail="该插件未提供 bundle.html")

    # 签名强制：受信任档要求 manifest HMAC 有效，并且签名载荷中的
    # bundle_sha256 与磁盘文件一致。仅检查 signature 非空会被任意字符串绕过，
    # 也挡不住 manifest 不变但 bundle.html 被替换。
    try:
        from clipwright.plugins.loader import verify_manifest_signature

        manifest = _loader._parse_manifest(plugin_id, _loader.plugin_dir / plugin_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"插件清单解析失败: {e}") from e
    if not getattr(manifest, "signature", ""):
        raise HTTPException(
            status_code=403,
            detail="受信任 UI bundle 需要插件签名（manifest.signature 缺失）",
        )
    if not verify_manifest_signature(manifest):
        raise HTTPException(status_code=403, detail="插件 manifest 签名校验失败")
    declared = str(getattr(manifest, "bundle_sha256", "") or "").strip().lower()
    if not declared:
        raise HTTPException(
            status_code=403,
            detail="受信任 UI bundle 需要 manifest.bundle_sha256（旧 bundle 请重新签发）",
        )
    try:
        html = bundle_file.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read bundle: {e}")
    import hashlib

    actual = hashlib.sha256(bundle_file.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual, declared):
        raise HTTPException(status_code=403, detail="bundle.html 内容与 manifest 声明的摘要不一致")
    return HTMLResponse(
        content=html,
        headers={
            # 沙箱内运行：禁止外联默认收窄到自身/内联（插件如需网络应在 manifest 声明并扩展）
            "Content-Security-Policy": "default-src 'self' 'unsafe-inline' data: blob:",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/prompt-symbols")
async def list_prompt_symbols(request: Request = None) -> dict[str, object]:
    """B5-T7：提示词符号清单与当前来源（诊断接口）。

    返回 `{constants: [...], builders: [...], errors: [...]}`；每条含
    `name` / `wired` / `override`（插件 id、来源）。**不展示提示词正文**——只给长度，
    避免把敏感上下文通过诊断接口泄漏出去。

    `wired=false` 的常量尚未接到 `resolve_constant()`（消费方仍是直接 import），
    如实标注，免得「能注册却没人用」被误认为已生效。
    """
    _require_admin(request)
    from clipwright.plugins import prompt_overrides

    payload = prompt_overrides.all_symbols()
    payload["errors"] = list(prompt_overrides.OVERRIDE_ERRORS[-20:])
    return payload


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

    # B6：内置镜像按 TTL 重扫——启动后才注册的内置 hook/prompt 也能出现在门面
    _maybe_refresh_builtin_mirror()

    filter_kind = None
    if kind:
        try:
            filter_kind = ContributionKind(kind)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"未知贡献类型: {kind}")

    # P6：`meta.admin_only` 的贡献只发给管理员。
    # 策略（产品确认）：插件页/界面默认对**所有登录用户**开放；插件若声明
    # admin_only，则由后端在这里直接不投递（非管理员连定义都拿不到，
    # 比前端隐藏更可靠——前端隐藏只是观感）。
    # off/token 模式没有用户身份（user_id=None）——按本仓库既有语义视为管理员
    # （与 `_require_admin` 一致），避免单机/本地部署丢掉 admin_only 插件页。
    from clipwright.authz import current_user_id as _uid
    from clipwright.authz import is_admin as _is_admin
    if request is None:
        requester_is_admin = False
    elif _uid(request) is None:
        requester_is_admin = True
    else:
        requester_is_admin = _is_admin(request)

    out: list[dict[str, object]] = []
    for owner_id, item in ContributionRegistry.list(filter_kind):
        if owner and owner_id != owner:
            continue
        if slot and str(item.meta.get("slot", "")) != slot:
            continue
        if item.meta.get("admin_only") and not requester_is_admin:
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
        discovered = _discover_plugins_cached()
        for pid in discovered:
            if owner and pid != owner:
                continue
            # C3：旧 ui.json 也要遵守「只对已加载且启用的 owner 输出」——
            # 否则插件被禁用后 settings.section 仍会渲染它的面板。
            if not _loader.is_enabled(pid) or _loader.get(pid) is None:
                continue
            raw = _legacy_ui_file(pid)
            if raw is None:
                continue
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
    # C3：风格解释器是「多注册、按 priority 取活跃项」——把当前活跃 owner 与
    # 候选项暴露出来，避免装了第二个插件却不知道没生效
    style_state: dict[str, object] = {}
    try:
        from clipwright.services.style_interpreter import StyleInterpreter
        style_state = {
            "active": StyleInterpreter._owner or "",
            "candidates": [
                {"owner": owner, "priority": priority}
                for owner, priority, _plugin in StyleInterpreter.list_plugins()
            ],
        }
    except Exception as e:  # noqa: BLE001 — 诊断信息缺失不影响健康总览
        logger.warning("风格解释器状态读取失败: %s", e)
    return {
        "overall": overall,
        "plugins": plugins,
        "errors_total": len(errors),
        "prompts_registered": sum(
            len(v) for v in PluginPromptRegistry.list_registered().values()),
        "style_interpreter": style_state,
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
    # B5：卸载改变了贡献集合，失效读路径缓存（扫盘 + ui.json）
    _invalidate_plugin_caches()
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
    config_path = _loader.get_plugin_data_dir(plugin_id) / "config.yaml"
    backup = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    try:
        _loader.save_config(plugin_id, data)
        # 未加载的插件（预配置）无需 reload：loader.reload 对未加载插件返回
        # False，旧实现据此回 500——保存成功却报错（审计 P1）。
        _loaded_now = _loader.get(plugin_id) is not None
        applied = _loader.reload(plugin_id) if _loaded_now else True
    except Exception as e:
        # C2：保存/重载失败必须还原旧文件与旧运行态，不能返回假成功。
        try:
            if backup is None:
                config_path.unlink(missing_ok=True)
            else:
                config_path.write_text(backup, encoding="utf-8")
            _loader.reload(plugin_id)
        except Exception as restore_error:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"配置更新失败且回滚失败: {e}; 回滚错误: {restore_error}",
            ) from e
        raise HTTPException(status_code=500, detail=f"配置更新失败，已回滚: {e}") from e
    if not applied:
        # 配置已落盘但插件没能用新配置起来（回滚到旧实例）：明确告诉调用方，
        # 不能回 200 让用户以为已生效。
        raise HTTPException(
            status_code=500,
            detail="配置已保存，但插件重载失败并已回滚到旧实例；请查看插件错误日志",
        )
    # B5：重载会重新注册贡献（例如钩子/槽位随配置变化），失效读路径缓存
    _invalidate_plugin_caches()
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
    backup = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    try:
        if config_path.exists():
            config_path.unlink()

        # 刷新并重载（未加载插件只删配置文件，不 reload）
        plugin = _loader.get(plugin_id)
        if plugin:
            from clipwright.plugins.loader import _extract_flat_values
            plugin.config = _extract_flat_values(_loader._get_merged_config(plugin_id))

        applied = _loader.reload(plugin_id) if plugin is not None else True
    except Exception as e:
        try:
            if backup is not None:
                config_path.write_text(backup, encoding="utf-8")
            _loader.reload(plugin_id)
        except Exception as restore_error:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"配置删除失败且回滚失败: {e}; 回滚错误: {restore_error}",
            ) from e
        raise HTTPException(status_code=500, detail=f"配置删除失败，已回滚: {e}") from e
    if not applied:
        raise HTTPException(
            status_code=500,
            detail="配置已删除，但插件重载失败并已回滚到旧实例；请查看插件错误日志",
        )
    # B5：重载后贡献集合可能变化，失效读路径缓存
    _invalidate_plugin_caches()
    return {"status": "ok", "plugin_id": plugin_id}


@router.post("/load-all", response_model=list[str])
async def load_all_plugins(request: Request) -> list[str]:
    """发现并加载所有可用插件。"""
    _require_admin(request)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    return _loader.load_all()


def _capability_grants_path(plugin_id: str) -> "Path":
    from pathlib import Path

    return _loader.get_plugin_data_dir(plugin_id) / "capability_grants.json"


def _load_capability_grants(plugin_id: str) -> list[str]:
    import json
    from pathlib import Path

    path: Path = _capability_grants_path(plugin_id)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏文件按空授权
        return []
    return sorted({str(x) for x in raw}) if isinstance(raw, list) else []


def _save_capability_grants(plugin_id: str, names: list[str]) -> None:
    import json

    path = _capability_grants_path(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(names)), ensure_ascii=False), encoding="utf-8")


@router.get("/{plugin_id}/capabilities")
async def get_plugin_capability_grants(plugin_id: str, request: Request) -> dict:
    """管理员登记的能力授权（默认放行项 + 额外 grant）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    from clipwright.services.plugin_rpc import DEFAULT_CAPABILITIES

    return {
        "plugin_id": plugin_id,
        "default": sorted(DEFAULT_CAPABILITIES),
        "granted": _load_capability_grants(plugin_id),
    }


@router.post("/{plugin_id}/capabilities/{name}")
async def grant_plugin_capability(plugin_id: str, name: str, request: Request) -> dict:
    """登记一个引擎能力（manifest 只能申请，授权在宿主侧）。"""
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if not name.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="非法能力名")
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    names = _load_capability_grants(plugin_id)
    if name not in names:
        names.append(name)
        _save_capability_grants(plugin_id, names)
    return {"plugin_id": plugin_id, "granted": sorted(set(names))}


@router.delete("/{plugin_id}/capabilities/{name}")
async def revoke_plugin_capability(plugin_id: str, name: str, request: Request) -> dict:
    _require_admin(request)
    _validate_plugin_id(plugin_id)
    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    names = [n for n in _load_capability_grants(plugin_id) if n != name]
    _save_capability_grants(plugin_id, names)
    return {"plugin_id": plugin_id, "granted": names}


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
    # C3：与 /contributions 的 legacy 合成同一口径——未加载/已禁用不返回布局，
    # 否则禁用插件后管理页仍能渲染它的表单（并可能触发其动作）。
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    if not _loader.is_enabled(plugin_id) or _loader.get(plugin_id) is None:
        return {"widgets": []}
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
        # 旧格式 → settings.section slot（前端 Slot 渲染器统一消费）。
        # 兼容期同时保留原始 title/widgets 顶层字段——插件管理页的「预览 UI」
        # 与既有第三方调用方仍按旧契约读取（D-1 曾丢掉这两个键导致预览失效）。
        title = str(raw.get("title") or "插件设置")
        return {
            "title": title,
            "widgets": raw.get("widgets") or [],
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


# ── W01：插件专用受限代理（服务端权限最终裁定）──────────────────────────
#
# 背景（审计 PRD §5D PL-01/02）：插件贡献里的 endpoint/method 此前由前端直发到
# 带 CLIPWRIGHT_API_TOKEN 的普通宿主 API —— 插件可以借宿主凭据 DELETE /api/project/*。
# 前端已做默认拒绝 + 白名单收口（pluginApiGuard），但**前端白名单不是授权边界**；
# 本端点是服务端最终裁定：按插件启停状态 + 服务端允许清单转发，普通宿主令牌
# 不因代理放宽。

#: 服务端允许清单：plugin_id → 允许的方法+动作。
#: key 形如 "GET:probe" / "POST:tool"；具体目标端点在 handler 里映射，不接受
#: 插件自报的任意路径（插件若需要新端点，必须改这里 + 发版）。
_PLUGIN_PROXY_ALLOWED: dict[str, dict[str, str]] = {
    # 内置生成类插件：ui.json 按钮只调 /api/tool/execute
    "ai_image_gen": {"POST:tool": "/api/tool/execute"},
    "ai_music_gen": {"POST:tool": "/api/tool/execute"},
    "ai_video_gen": {"POST:tool": "/api/tool/execute"},
    "example_full": {"POST:tool": "/api/tool/execute"},
    # 依赖探针：GET /api/render/browser（前端浮层轮询）+ POST 安装触发（用户点确认）
    "headless_chrome": {
        "GET:probe": "/api/render/browser",
        "POST:install": "/api/render/browser/install",
    },
}


class _PluginProxyBody(BaseModel):
    """代理请求体。action 由服务端清单解释，插件/前端不能指定任意路径。"""
    action: str  # "tool" | "probe" | "install"
    payload: dict = {}
    #: 新版 UI 传贡献 id，服务端核对其当前确属 URL 中的 owner；旧客户端可省略。
    contribution_id: str = ""


def _proxy_target(method_norm: str, action: str, owner: str) -> str | None:
    """大小写无关地查白名单；method_norm 是 get/post，字典键是 GET/POST。"""
    allowed = _PLUGIN_PROXY_ALLOWED.get(owner) or {}
    return allowed.get(f"{method_norm.upper()}:{action}")


def _plugin_owns_tool(owner: str, tool_name: str) -> bool:
    """工具名必须属于该插件当前已注册的贡献，而非宿主/其他插件工具。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
    from clipwright.tool.registry import ToolRegistry

    declared = {
        item.id for pid, item in ContributionRegistry.list(ContributionKind.TOOL)
        if pid == owner
    }
    if tool_name not in declared:
        return False
    tool = ToolRegistry.get(tool_name)
    return tool is not None and str(getattr(tool, "_plugin_id", "") or "") == owner


def _contribution_owned_by(plugin_id: str, raw: str) -> bool:
    """校验前端上报的贡献 id 确属该插件。

    契约（B6，2026-11-13 实测修正）：
    - 空串 = 旧客户端未上报，跳过校验（行为不变）；
    - 裸 id（前端规范形状）= 门面 `(ui_slot, id)` 的 owner 必须等于 plugin_id；
    - ``owner:id`` 也接受（旧版本前端把 stateKey 当 id 发），但 owner 段必须一致；
    - `{plugin_id}_legacy` 是 `/contributions` 现造的旧 ui.json 槽，不进门面，
      按命名规则归属该插件。

    旧实现只按裸 id 查门面，前端发 `owner:id` 时恒 403，等于把布局按钮全打死。
    """
    value = str(raw or "").strip()
    if not value:
        return True
    owner_part, sep, rest = value.partition(":")
    cid = rest if sep else value
    if sep and owner_part != plugin_id:
        return False
    if not cid:
        return False
    # 旧 ui.json 槽：`<plugin>_legacy` 是 /contributions 现造的 id；`legacy` 是旧前端
    # 把 stateKey 当 id 发时被剥前缀后的形状（`pluginId:legacy`）。两者都只在该插件
    # 确实有 ui.json 时放行，避免任意插件冒领。
    if cid == f"{plugin_id}_legacy" or cid == "legacy":
        return _legacy_ui_file(plugin_id) is not None
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    return ContributionRegistry.owner_of(ContributionKind.UI_SLOT, cid) == plugin_id


@router.post("/proxy/{plugin_id}/{method}")
async def plugin_proxy(plugin_id: str, method: str, body: _PluginProxyBody,
                       request: Request = None) -> dict:
    """插件专用受限代理：服务端按清单裁定，普通宿主 API 不因代理放宽。

    - 只接受 POST（GET 探针走 `method="get"` 形式 + action=probe）；
    - plugin_id 必须已加载且启用；
    - (plugin_id, method, action) 必须命中 `_PLUGIN_PROXY_ALLOWED`，目标端点由
      服务端映射，不接受请求体里的路径。
    """
    _validate_plugin_id(plugin_id)
    method_norm = method.strip().lower()
    if method_norm not in ("get", "post"):
        raise HTTPException(status_code=405, detail=f"代理不支持方法: {method}")

    if _loader is None:
        raise HTTPException(status_code=503, detail="Plugin system not initialized")
    if plugin_id not in _loader.discover():
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    if not _loader.is_enabled(plugin_id):
        # 插件被禁用后其贡献即使仍缓存在前端也不能再调用（默认拒绝）
        raise HTTPException(status_code=403, detail=f"插件 {plugin_id} 已禁用")
    if _loader.get(plugin_id) is None:
        raise HTTPException(status_code=403, detail=f"插件 {plugin_id} 未加载")
    if body.contribution_id and not _contribution_owned_by(plugin_id, body.contribution_id):
        raise HTTPException(status_code=403, detail="贡献 id 与插件归属不匹配")

    target = _proxy_target(method_norm, body.action, plugin_id)
    if not target:
        raise HTTPException(
            status_code=403,
            detail=f"插件 {plugin_id} 未被授权调用 {method_norm}:{body.action}",
        )

    # 服务端内部转发（复用既有 handler 逻辑，不出网）
    if method_norm == "post" and body.action == "tool":
        from clipwright.api.tool import _validate_tool_params
        from clipwright.schema.tool import ToolExecResult
        from clipwright.tool import ToolRegistry

        name = str(body.payload.get("name") or "")
        params = body.payload.get("params") or {}
        if not name:
            raise HTTPException(status_code=400, detail="缺少工具名")
        if not _plugin_owns_tool(plugin_id, name):
            raise HTTPException(
                status_code=403,
                detail=f"工具 {name} 不属于插件 {plugin_id} 的已注册贡献",
            )
        tool = ToolRegistry.get(name)
        tier = str(getattr(tool, "permission_tier", "") or "")
        if tier == "dangerous":
            raise HTTPException(
                status_code=403,
                detail=f"插件 UI 不允许直接调用 dangerous 档工具：{name}",
            )
        # costly 档允许（生成类插件的主按钮本就是这个档位），但调用方须在
        # 前端逐次确认；服务端仍按统一 ToolRegistry 入口记录费用与审计。
        _validate_tool_params(params)
        result = await ToolRegistry.execute(name, **params)
        if result.status == "not_found":
            raise HTTPException(status_code=404, detail=result.error)
        return result.model_dump()

    if method_norm == "get" and body.action == "probe":
        # 探针只允许打渲染依赖状态端点；直接调用其 handler 逻辑
        from clipwright.api.render import render_browser_status
        return await render_browser_status()

    if method_norm == "post" and body.action == "install":
        from clipwright.api.render import render_browser_install
        return await render_browser_install()

    raise HTTPException(status_code=403, detail="未授权的代理动作")
