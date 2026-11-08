"""Provider 插件化与提示词覆盖的**验收自检脚本**（非 pytest）。

为什么单独写一个脚本：本项目的测试由人执行（`python -m pytest tests -q`），而这次改造涉及
「注册表归属 / 能力网关 / 提示词覆盖 / 静态纯度检查」等**运行期契约**，散在会话里的临时断言
无法复用。本脚本把这些断言固化成一条命令，人和 CI 都能跑：

    py -3.14 scripts/verify_provider_pluginization.py           # 全部检查
    py -3.14 scripts/verify_provider_pluginization.py -k prompt  # 只跑名字含 prompt 的检查

它**不依赖 pytest**，也不改动任何落盘数据（provider_sources 的读写被 monkeypatch 到临时文件）。
退出码 0 = 全过；1 = 有失败项（会打印失败原因）。

覆盖的契约（对应 PRD §15）：
- 五类 provider、11 个 id 全部由插件提供（内置不得再持有）
- 能力网关 `POST /api/capability/{name}` 的 404 / 200 语义
- `/api/providers/schemas` 与 `/api/providers/capabilities` 的数据形状
- 提示词：26 常量 + 8 builder 全部 `wired`，覆盖→解析→回滚闭环
- builder 四关：签名形状 / 静态纯度 / 双 dry-render / 未知符号
- 不变量 #12：`capability_matrix.TIER_MAP` 必须为空
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def deco(fn: Callable[[], None]) -> Callable[[], None]:
        fn._check_name = name  # type: ignore[attr-defined]
        return fn

    return deco


# ── 检查项 ────────────────────────────────────────────────


@check("provider_owners")
def check_provider_owners() -> None:
    """五类 provider 的全部 id 都必须由插件提供（内置只允许以「未接管」形式存在）。"""
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.plugins.contribution import ContributionKind as K
    from clipwright.plugins.contribution import ContributionRegistry as R

    expected = {
        K.LLM_PROVIDER: {"anthropic", "openai", "ollama"},
        K.TTS_PROVIDER: {"cosyvoice", "minimax", "qwen-tts"},
        K.EMBEDDING_PROVIDER: {"openai", "ollama", "local"},
        K.RERANK_PROVIDER: {"http", "local"},
        K.STT_PROVIDER: {"whisper", "faster-whisper"},
    }
    with TestClient(app, raise_server_exceptions=False):
        for kind, ids in expected.items():
            actual = {c.id for _, c in R.list(kind)}
            missing = ids - actual
            assert not missing, f"{kind.value} 缺 id: {sorted(missing)}"
            builtin = {
                c.id for _, c in R.list(kind)
                if R.owner_of(kind, c.id) in ("", "__builtin__")
            }
            assert not builtin, f"{kind.value} 仍由内置提供: {sorted(builtin)}"


@check("providers_available_and_schemas")
def check_available_and_schemas() -> None:
    from fastapi.testclient import TestClient

    from clipwright.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        av = c.get("/api/providers/available").json()
        for key, ids in (
            ("llm_provider", {"anthropic", "openai", "ollama"}),
            ("tts_provider", {"cosyvoice", "minimax", "qwen-tts"}),
            ("embedding_provider", {"openai", "ollama", "local"}),
            ("rerank_provider", {"http", "local"}),
        ):
            assert ids <= set(av.get(key) or []), f"{key} 下拉缺 {sorted(ids - set(av.get(key) or []))}"

        for kind, ids in (
            ("llm_provider", {"openai", "ollama", "anthropic"}),
            ("tts_provider", {"cosyvoice", "minimax", "qwen-tts"}),
        ):
            rows = c.get(f"/api/providers/schemas?kind={kind}").json()["schemas"][kind]
            got = {r["id"] for r in rows}
            assert ids <= got, f"{kind} 缺 schema: {sorted(ids - got)}"
            for r in rows:
                assert r["plugin_id"], f"{kind}:{r['id']} 没有 plugin_id"


@check("providers_capabilities")
def check_capabilities() -> None:
    from fastapi.testclient import TestClient

    from clipwright.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/providers/capabilities?kind=llm&provider=openai&model=gpt-x")
        assert r.status_code == 200, f"capabilities 返回 {r.status_code}"
        body = r.json()
        assert body["capabilities"], "capabilities 为空"
        assert body["plugin_id"] == "llm_openai_compatible", body.get("plugin_id")
        # reasoning 能力必须来自实现，不允许前端猜
        assert "reasoning_content" in body["capabilities"], "缺 reasoning_content"

        # 未安装的 provider：200 + error（不是 404——诊断接口要能显示「没装」）
        r2 = c.get("/api/providers/capabilities?kind=llm&provider=does-not-exist")
        assert r2.status_code == 200, f"未安装 provider 应返回 200，实际 {r2.status_code}"
        assert r2.json()["capabilities"] is None and r2.json()["error"], "未安装时应有 error 说明"


@check("capability_gateway")
def check_capability_gateway() -> None:
    from fastapi.testclient import TestClient

    from clipwright.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        ok = c.post("/api/capability/list_animations", json={"params": {}})
        assert ok.status_code == 200, f"网关 200 路径失败: {ok.status_code} {ok.text[:120]}"
        assert ok.json()["status"] == "success", ok.json().get("status")

        bad = c.post("/api/capability/no_such_capability_xyz", json={"params": {}})
        assert bad.status_code == 404, f"未知能力应 404，实际 {bad.status_code}"


@check("prompt_symbols_wired")
def check_prompt_symbols() -> None:
    from fastapi.testclient import TestClient

    from clipwright.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/plugin/prompt-symbols").json()
        consts, builders = body["constants"], body["builders"]
        assert len(consts) == 26, f"常量数 {len(consts)} != 26"
        assert len(builders) == 8, f"builder 数 {len(builders)} != 8"
        unwired = [x["name"] for x in consts + builders if not x["wired"]]
        assert not unwired, f"仍存在未接线符号（覆盖了也不生效）: {unwired}"
        # 不返回提示词正文（只给长度）
        assert all("chars" in x for x in consts), "常量缺 chars"
        assert all("signature" in x for x in builders), "builder 缺 signature"


@check("prompt_override_roundtrip")
def check_override_roundtrip() -> None:
    from clipwright import prompts as P
    from clipwright.plugins import prompt_overrides as O

    # 常量
    base = O.resolve_constant("LOOP_BUDGET_HEADER")
    assert not O.register_constant_override("_verify", "LOOP_BUDGET_HEADER", "【自检】{remaining}"), "应接受"
    assert O.resolve_constant("LOOP_BUDGET_HEADER") == "【自检】{remaining}"
    # 覆盖要真的影响 builder 输出
    assert P.build_budget_note(remaining=3, max_turns=10, used=1).startswith("【自检】")
    O.clear_plugin("_verify")
    assert O.resolve_constant("LOOP_BUDGET_HEADER") == base, "回滚失败"

    # builder
    def plugin_note(remaining: int, max_turns: int, used: int) -> str:
        return f"PLUGIN {remaining}/{max_turns}/{used}"

    assert not O.register_builder_override("_verify", "build_budget_note", plugin_note), "应接受"
    assert O.safe_builder("build_budget_note")(remaining=2, max_turns=8, used=1) == "PLUGIN 2/8/1"
    O.clear_plugin("_verify")
    assert O.safe_builder("build_budget_note") is P.build_budget_note, "builder 回滚失败"


@check("prompt_runtime_path")
def check_prompt_runtime_path() -> None:
    """提示词**运行时装配点**真的读解析层（而不是导入期快照）。

    为什么单列一项：`prompt_symbols_wired` 只证明符号登记过、`prompt_override_roundtrip`
    只直接调 `resolve_constant`/`safe_builder`。两者都不断言「真正拼 prompt 的那个函数」
    走了覆盖层——只要有人把 `_p(...)` 换回模块级常量，前两项照样全绿，插件覆盖却永久失效。
    这里用假 task 直接调 `agent_loop._system_prompt()`（主 agent 每一步都用它），
    并验证导入期快照 `_NORMAL_SYSTEM` 保持默认值（那是给诊断/测试引用用的，不该被覆盖改写）。
    """
    from types import SimpleNamespace

    from clipwright import prompts as P
    from clipwright.plugins import prompt_overrides as O
    from clipwright.services import agent_loop as L

    def task(plan_only: bool, closeout: str = "normal") -> SimpleNamespace:
        return SimpleNamespace(
            request=SimpleNamespace(plan_only=plan_only),
            closeout=SimpleNamespace(mode=closeout),
        )

    assert L._system_prompt(task(True)) == P.LOOP_PLAN_ONLY_SYSTEM, "plan_only 分支没读 prompts"
    assert L._system_prompt(task(False)) == P.LOOP_NORMAL_SYSTEM, "正常分支没读 prompts"
    assert L._system_prompt(task(False, "soft_landing")) == (
        P.LOOP_NORMAL_SYSTEM + P.LOOP_CLOSEOUT_BLOCK
    ), "收口分支没拼上 LOOP_CLOSEOUT_BLOCK"

    # 覆盖要能在**运行时装配点**生效（导入期快照不动）
    assert not O.register_constant_override(
        "_verify", "LOOP_NORMAL_SYSTEM", "【自检】运行时装配点"
    ), "覆盖应被接受"
    try:
        assert L._system_prompt(task(False)) == "【自检】运行时装配点", "运行时装配点没走覆盖层"
        assert L._NORMAL_SYSTEM == P.LOOP_NORMAL_SYSTEM, "导入期快照被覆盖改写了"
    finally:
        O.clear_plugin("_verify")
    assert L._system_prompt(task(False)) == P.LOOP_NORMAL_SYSTEM, "覆盖回滚后未恢复默认"


@check("display_spec_contract_sync")
def check_display_spec_contract_sync() -> None:
    """\u63d2\u4ef6\u5951\u7ea6\u4e0e\u524d\u7aef\u526f\u672c\u5fc5\u987b**\u9010\u5b57\u8282**\u4e00\u81f4\uff08\u5355\u4e00\u771f\u76f8\u5728\u63d2\u4ef6\uff09\u3002

    组件白名单与上限原来靠两仓手抄；改一边忘另一边 → 半渲染 / 前后端判断不一致。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    backend = root / "plugins/genui/contract.json"
    frontend = Path(r"J:\Clipweight-Client\src\features\genui\contract.json")
    assert backend.exists(), f"缺少后端契约文件：{backend}"
    if not frontend.exists():
        print("  （前端仓库不在本机：跳过跨仓字节比对）")
        return
    assert backend.read_bytes() == frontend.read_bytes(), (
        "插件契约与前端副本不一致——改一边必须同步另一边"
    )


@check("display_spec_plugin_contract")
def check_display_spec_plugin_contract() -> None:
    """GenUI 插件：manifest ↔ contributions() 对齐；契约可被引擎归一；不覆盖内置提示词。"""
    from pathlib import Path

    import yaml

    from clipwright.plugins import prompt_overrides as O
    from clipwright.plugins.contribution import ContributionKind
    from clipwright.services.display_spec import normalize_contract
    from plugins.genui import main as M

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load(
        (root / "plugins/genui/plugin.yaml").read_text(encoding="utf-8")
    ) or {}
    declared = {(str(i.get("kind")), str(i.get("id"))) for i in (manifest.get("contributions") or [])}
    items = list(M.GenUIPlugin().contributions() or [])
    actual = {(it.kind.value, it.id) for it in items}
    assert declared == actual, f"manifest 与 contributions() 不一致：declared={declared} actual={actual}"
    assert len(items) == 3, f"应为 1 条 PROMPT + 2 条 display_spec，实际 {len(items)}"

    prompt_item = next(it for it in items if it.kind is ContributionKind.PROMPT)
    assert prompt_item.meta.get("agent") == "requirements", prompt_item.meta
    assert str(prompt_item.meta.get("prompt") or "").strip(), "PROMPT 贡献不能为空"

    spec_items = [it for it in items if it.kind is ContributionKind.DISPLAY_SPEC]
    assert {it.id for it in spec_items} == {"brief", "plan"}, [it.id for it in spec_items]
    for item in spec_items:
        assert item.object is None, "display_spec 是纯数据：object 必须为 None"
        contract, problems = normalize_contract(item.meta, owner="genui")
        assert contract is not None, f"{item.id} 契约形状不合法：{problems}"
        assert contract.spec_id == item.id
        assert contract.recipe and contract.recipe.get("nodes"), f"{item.id} 缺少 recipe.nodes"
    assert next(it for it in spec_items if it.id == "plan").meta["trust"] == "recipe_only"

    symbols = {str(x["name"]) for x in (O.all_symbols().get("constants") or [])}
    assert not any("genui" in name.lower() for name in symbols), (
        f"插件不应注册提示词符号覆盖：{sorted(symbols)}"
    )


@check("display_spec_contract_frontend_subset")
def check_display_spec_contract_frontend_subset() -> None:
    """契约声明的组件/动作必须 ⊆ 前端已实现集合（前端原语是编译期闭包）。

    前端仓库不在本机时按既有约定跳过（跨仓检查不应把单仓 CI 弄红）。
    """
    import json
    import re
    from pathlib import Path

    frontend_root = Path(r"J:\Clipweight-Client")
    contract_path = frontend_root / "src/features/genui/contract.json"
    types_path = frontend_root / "src/features/genui/types.ts"
    if not (contract_path.exists() and types_path.exists()):
        print("  （前端仓库不在本机：跳过子集断言）")
        return
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    types_src = types_path.read_text(encoding="utf-8")

    def _tuple_block(name: str) -> set[str]:
        match = re.search(name + r"\s*=\s*\[(.*?)\]", types_src, re.S)
        assert match, f"前端 types.ts 里找不到 {name}"
        return set(re.findall(r"'([^']+)'", match.group(1)))

    implemented_components = _tuple_block("GENUI_COMPONENT_TYPES")
    implemented_actions = _tuple_block("GENUI_ACTION_KINDS")
    extra_components = [c for c in contract["components"] if c not in implemented_components]
    extra_actions = [a for a in contract["actions"] if a not in implemented_actions]
    assert not extra_components, f"契约声明了前端未实现的组件：{extra_components}"
    assert not extra_actions, f"契约声明了前端未实现的动作：{extra_actions}"


@check("display_spec_off_degradation")
def check_display_spec_off_degradation() -> None:
    """插件被禁用（提示词注册表空 + 没有 display_spec 契约）时的行为（A6 口径）。

    2026-11-07 用户裁定：能力归插件 → **禁用即没有展示 spec**（推翻旧的"确定性兜底"）。
    但业务写入必须照常：`ensure_display_specs()` 在无契约时**原样返回 payload**、不建空容器。
    """
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    from clipwright.services.display_spec import ensure_display_specs
    from clipwright.services.display_spec.contract import list_contracts
    from clipwright.services.requirements_service import _brief_system_prompt

    saved = dict(PluginPromptRegistry._prompts)
    saved_specs = [
        (owner, item) for owner, item in ContributionRegistry.list(ContributionKind.DISPLAY_SPEC)
    ]
    try:
        PluginPromptRegistry._prompts = {}
        for owner, _item in saved_specs:
            ContributionRegistry.unregister_plugin(owner)

        prompt = _brief_system_prompt("")
        assert prompt.strip(), "禁用插件后 base 提示词必须还在"
        assert "GenUI 展示规范" not in prompt, "禁用插件后不应再出现 GenUI 规范段"

        assert list_contracts() == [], "测试前提：此时应没有任何 display_spec 契约"
        payload = {"title": "T", "overview": "O"}
        out = ensure_display_specs("brief", payload)
        assert out is payload, "无契约时应原样返回（不阻断业务写入）"
        assert "display_specs" not in payload, "无契约时不得凭空创建展示容器"
    finally:
        PluginPromptRegistry._prompts = saved
        for owner, item in saved_specs:
            ContributionRegistry.declared_scope(owner, [item.key])
            ContributionRegistry.register(owner, [item])


@check("display_spec_kind_registered")
def check_display_spec_kind_registered() -> None:
    """第 18 类扩展点真的在位：枚举存在、不进 `_WIRED_KINDS`、子进程档直通、运行期有契约。"""
    import re
    from pathlib import Path

    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.plugins import contribution_dispatch as D
    from clipwright.plugins.contribution import ContributionKind

    assert ContributionKind("display_spec") is ContributionKind.DISPLAY_SPEC
    assert ContributionKind.DISPLAY_SPEC not in D._WIRED_KINDS, "display_spec 不应有后端注册表"
    assert ContributionKind.DISPLAY_SPEC in D._DATA_ONLY_KINDS, "应为纯数据 kind"

    loader_src = (Path(__file__).resolve().parents[1] / "clipwright/plugins/loader.py").read_text(
        encoding="utf-8"
    )
    block = re.search(r"_DECLARATIVE_KINDS\s*=\s*{[^}]*}", loader_src, re.S)
    assert block and "DISPLAY_SPEC" in block.group(0), "子进程档应直通 display_spec"

    from clipwright.services.display_spec import list_contracts

    with TestClient(app):
        contracts = list_contracts()
    ids = {c.spec_id for c in contracts}
    assert ids == {"brief", "plan"}, f"默认 profile 下的契约应恰好是 brief/plan，实际 {ids}"
    for contract in contracts:
        assert contract.components and contract.limits, contract.spec_id


@check("display_spec_no_engine_path")
def check_display_spec_no_engine_path() -> None:
    """PRD §8 判据 D1/D2/D3/D4：engine 里不能再有 genui 专属链路。

    D3 允许**老数据键兼容**（`genui` 字面量 ≤5 处且 0 处 import）；超出就说明有人在
    engine 里又把能力知识写回来了。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    engine = root / "clipwright"

    # D1：专属实现文件不存在
    assert not (engine / "schema/genui.py").exists(), "clipwright/schema/genui.py 应已删除"
    assert not (engine / "schema/genui_contract.json").exists(), "genui_contract.json 应已删除"

    py_files = list(engine.rglob("*.py"))
    text = {f: f.read_text(encoding="utf-8") for f in py_files}

    # D2：无专属导入
    imports = [
        str(f.relative_to(root))
        for f, body in text.items()
        if "from clipwright.schema.genui" in body or "import clipwright.schema.genui" in body
    ]
    assert not imports, f"仍有 schema.genui 导入：{imports}"

    # D4：无专属常量
    consts = [
        str(f.relative_to(root))
        for f, body in text.items()
        if re.search(r"\bGENUI_[A-Z_]+\s*=", body)
    ]
    assert not consts, f"engine 里仍有 GENUI_ 常量：{consts}"

    # D3：字面量收口（允许老键兼容）
    hits: list[str] = []
    for f, body in text.items():
        for i, line in enumerate(body.splitlines(), 1):
            if '"genui"' in line:
                hits.append(f"{f.relative_to(root)}:{i}: {line.strip()[:80]}")
    assert len(hits) <= 5, f"engine 里 \"genui\" 字面量过多（{len(hits)}）：\n" + "\n".join(hits)


@check("provider_impls_in_plugins")
def check_provider_impls_in_plugins() -> None:
    """协议实现必须住在插件，engine 只留协议/注册表（B7-T7.3/T7.4 已迁的三家族）。

    为什么现在就加：迁移是分批做的，若不加门禁，下一轮很容易"顺手"把某个实现搬回
    engine（就像当初的懒注册补位）。这里只锁**已完成的家族**，llm/tts 在 T7.1/T7.2
    完成后会追加进清单。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    engine = root / "clipwright"

    # 已迁家族（embedding / rerank / stt / llm / tts）：实现类名 → 插件模块
    # （必须既有实现、又不再出现在 engine 的 class 定义里）。
    moved_classes = {
        "OpenAIEmbeddingProvider": "plugins/embedding_providers/providers.py",
        "OllamaEmbeddingProvider": "plugins/embedding_providers/providers.py",
        "LocalEmbeddingProvider": "plugins/embedding_providers/providers.py",
        "RemoteEmbedder": "plugins/embedding_providers/providers.py",
        "HttpRerankProvider": "plugins/rerank_providers/providers.py",
        "LocalRerankProvider": "plugins/rerank_providers/providers.py",
        "HttpReranker": "plugins/rerank_providers/providers.py",
        "LocalCrossEncoder": "plugins/rerank_providers/providers.py",
        "probe_whisper": "plugins/stt_providers/backends.py",
        "transcribe_whisper": "plugins/stt_providers/backends.py",
        "transcribe_faster_whisper": "plugins/stt_providers/backends.py",
        "AnthropicProvider": "plugins/llm_anthropic/provider.py",
        "OpenAIProvider": "plugins/llm_openai_compatible/provider.py",
        "OllamaProvider": "plugins/llm_openai_compatible/provider.py",
        "QwenTTSProvider": "plugins/tts_qwen/provider.py",
        "CosyVoiceProvider": "plugins/tts_cosyvoice/provider.py",
        "MiniMaxProvider": "plugins/tts_minimax/provider.py",
    }
    for plugin_rel in set(moved_classes.values()):
        plugin_file = root / plugin_rel
        assert plugin_file.exists(), f"插件实现文件缺失：{plugin_rel}"
        body = plugin_file.read_text(encoding="utf-8")
        for name, target in moved_classes.items():
            if target != plugin_rel:
                continue
            assert re.search(rf"(class|def) {name}\b", body), f"{plugin_rel} 里找不到 {name}"

    engine_text = {
        f: f.read_text(encoding="utf-8") for f in engine.rglob("*.py")
    }
    for name, plugin_rel in moved_classes.items():
        offenders = [
            str(f.relative_to(root))
            for f, body in engine_text.items()
            if re.search(rf"^class {name}\b", body, re.M)
            or re.search(rf"^def {name}\b", body, re.M)
        ]
        assert not offenders, f"{name} 仍定义在 engine：{offenders}（应在 {plugin_rel}）"

    # 懒注册补位必须消失（它会让"禁用插件"不改变行为）
    joined = "".join(engine_text.values())
    for name in (
        "ensure_embedding_providers_registered",
        "ensure_rerank_providers_registered",
        "ensure_stt_providers_registered",
        "ensure_llm_providers_registered",
    ):
        assert name not in joined, f"engine 里仍有懒注册补位：{name}"


@check("provider_name_branches_removed")
def check_provider_name_branches_removed() -> None:
    """已清理家族不得再出现"按 provider 名分支"（T7.5 分步执行）。

    全仓级 `no_vendor_branches`（厂商字面量 ≤3）还没达标：`model_listing` /
    `rag.embedder` / `vision` / `mg.visual_eval` / `agent_loop` 里仍有按 id 的判断，
    已登记为 T7.5 剩余项。本检查先锁**已经清完的**三处，防止回退。
    """
    from pathlib import Path

    import clipwright.services.voice as voice_mod

    root = Path(__file__).resolve().parents[1]
    forbidden = {
        "clipwright/services/llm.py": ('provider == "anthropic"', 'self.provider == "anthropic"'),
        "clipwright/services/voice.py": (
            'provider_name == "qwen-tts"',
            "_LEGACY_PROVIDER_ALIASES",
        ),
        "clipwright/api/providers.py": ('"qwen_tts"',),
        "clipwright/services/vision.py": ('vision_prov == "anthropic"',),
        "clipwright/services/agent_loop.py": ('("ollama",)',),
        "clipwright/animation/mg/visual_eval.py": ('provider == "anthropic"',),
        "clipwright/providers/model_listing.py": (
            "SUPPORTED_LISTING_PROVIDERS",
            '"anthropic"',
            '"ollama"',
        ),
        "clipwright/api/render.py": ('or "local"', '("", "local")'),
        "clipwright/rag/embedder.py": (
            'provider == "sentence_transformer"',
            'provider in ("openai", "ollama")',
        ),
    }
    for rel, needles in forbidden.items():
        body = (root / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in body, f"{rel} 仍含按 provider 名的分支/字面量：{needle}"

    # T7.1 的兼容别名必须真的删掉（engine 里不该再有 TTS 实现类名）
    for name in ("QwenTTSProvider", "CosyVoiceProvider", "MiniMaxProvider"):
        assert not hasattr(voice_mod, name), f"services/voice.py 仍暴露 {name}（兼容别名未删）"


@check("no_vendor_branches")
def check_no_vendor_branches() -> None:
    """B7 收口：engine 里不得再有任何"按 provider 身份"的分支或内置白名单。

    为什么是语义检查而不是字面量计数：`"anthropic"` 这个词在 engine 里还有两类别用法——
    (1) **工具/消息格式词表**（`to_llm_tool(fmt="anthropic")`、`tool_result_style()=="anthropic"`），
    这是协议词汇，不是厂商分支；(2) **settings 默认值**（`llm_provider = "anthropic"`）。
    真正要禁的是"用 id 判断行为"：`provider == "qwen-tts"`、`provider in ("openai","ollama")`、
    `SUPPORTED_*` 名单。这条门禁直接断言后者为 0。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    engine = root / "clipwright"

    branch_re = re.compile(
        r"(provider|vision_prov|vision_provider|provider_name|llm_provider|tts_default_provider)"
        r"\s*(==|!=|\bin\b)\s*[\(\[]?\s*[\"'\']"
        r"(anthropic|ollama|cosyvoice|minimax|qwen-tts)"
    )
    offenders: list[str] = []
    for f in engine.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if branch_re.search(line):
                offenders.append(f"{f.relative_to(root)}:{i}: {line.strip()[:90]}")
    assert not offenders, "engine 里仍有按 provider 身份的分支：\n" + "\n".join(offenders)

    # 内置白名单/顺序表也必须消失（懒注册与硬编码顺序的残留形态）
    joined = "".join(
        f.read_text(encoding="utf-8") for f in engine.rglob("*.py") if "__pycache__" not in f.parts
    )
    for needle in ("SUPPORTED_LISTING_PROVIDERS", "_BUILTIN_ORDER", "SUPPORTED_LISTING"):
        assert needle not in joined, f"engine 里仍有内置 provider 白名单：{needle}"


@check("headless_impl_in_plugin")
def check_headless_impl_in_plugin() -> None:
    """B8-T8.1：chrome-headless-shell 的查找/安装/探测必须只在插件里。

    为什么要锁：这段代码会**下载 120MB 二进制并落盘**，属于典型插件能力
    （可审计、可禁用、可替换）。engine 只留门面与两档策略编排。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    engine = (root / "clipwright/services/headless_browser.py").read_text(encoding="utf-8")
    plugin = root / "plugins/headless_chrome/shell.py"
    assert plugin.exists(), "缺少 plugins/headless_chrome/shell.py（实现应在插件里）"
    body = plugin.read_text(encoding="utf-8")
    for needle in ("def find_shell", "def install_shell", "def install_by_download", "def probe_version"):
        assert needle in body, f"插件实现缺少 {needle}"
    # engine 门面不得再出现下载/子进程细节（这些是实现，不是编排）
    for needle in ("@puppeteer/browsers", "last-known-good-versions.json",
                   "zipfile", "create_subprocess_exec", "def _download", "def _http_json"):
        assert needle not in engine, f"engine 门面里仍有实现细节：{needle}"
    # 门面按能力形状取后端，不认死 id
    assert "_shell_backend" in engine and "callable(obj.get(\"resolve\"))" in engine


@check("dead_hooks_removed")
def check_dead_hooks_removed() -> None:
    """B8-T8.3/T8.4：两个"声明了但 engine 不消费"的插件能力必须收窄。

    - `kinetic_typography`：删死钩子与没人消费的 CSS；动画必须是带 keyframes 的
      `AnimationDef`（否则"注册了动画但渲不出来"）；
    - `lottie_animations`：删两个形状错误的 hook 与外链 CDN 模板，只留素材目录说明。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    kinetic = (root / "plugins/kinetic_typography/main.py").read_text(encoding="utf-8")
    lottie = (root / "plugins/lottie_animations/main.py").read_text(encoding="utf-8")

    for needle in ("catalog", "animation_catalog_extend"):
        assert needle not in kinetic, f"kinetic_typography 仍含死钩子痕迹：{needle}"
    for needle in ("cdnjs", "renderers", "lottie_web"):
        assert needle not in lottie, f"lottie_animations 仍含假渲染器痕迹：{needle}"

    # 动画贡献必须真的带关键帧（空 keyframes = 注册了但渲不出来）
    import importlib

    mod = importlib.import_module("plugins.kinetic_typography.main")
    anims = [
        c for c in mod.KineticTypographyPlugin().contributions()
        if getattr(c.kind, "value", "") == "animation"
    ]
    assert anims, "kinetic_typography 没有动画贡献"
    for c in anims:
        kfs = getattr(c.object, "keyframes", None) or []
        assert len(kfs) >= 2, f"动画 {c.id} 的 keyframes 少于 2 帧（无法插值）"


@check("export_preset_wiring")
def check_export_preset_wiring() -> None:
    """B8-T8.5：平台预设必须以 `export_preset` 纯数据贡献，且 hook 真的能改参数。

    旧实现的两个病：预设写死在插件里但 hook 拿不到上下文（从未执行）；
    engine 另外维护一份 `_PLATFORM_EXPORT_COUNTS` 平台表。两处都必须消失。
    """
    import asyncio
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    engine = (root / "clipwright/api/render.py").read_text(encoding="utf-8")
    assert "_PLATFORM_EXPORT_COUNTS" not in engine, "engine 仍有硬编码平台封面表"
    assert "_preset_map" in engine, "engine 缺少预设合并函数"

    from clipwright.plugins.contribution import ContributionRegistry
    from plugins.platform_export.main import PlatformExportPlugin

    owner = "verify_platform_export"
    items = PlatformExportPlugin().contributions()
    presets = [c for c in items if getattr(c.kind, "value", "") == "export_preset"]
    assert len(presets) >= 6, f"平台预设贡献不足：{len(presets)}"
    for c in presets:
        assert c.object is None, f"export_preset:{c.id} 必须是纯数据（object=None）"

    ContributionRegistry.declared_scope(owner, [c.key for c in items])
    report = ContributionRegistry.register(owner, items)
    assert report.ok, report.rejected
    try:
        from clipwright.api.render import _preset_map
        from clipwright.plugins.hooks import HookPoint
        from clipwright.plugins.hooks_async import execute_async

        merged = _preset_map()
        for pid in ("bilibili", "douyin", "youtube", "xiaohongshu"):
            assert pid in merged, f"插件预设 {pid} 未进入 /presets 合并结果"

        ctx = {"task_id": "verify", "preset": "douyin", "settings": {}}
        asyncio.run(execute_async(HookPoint.PRE_RENDER, ctx))
        assert ctx["settings"].get("height") == 1920, "PRE_RENDER 没有把竖屏参数并进 settings"
    finally:
        ContributionRegistry.unregister_plugin(owner)

    # A6：禁用插件后预设消失（不做内置兜底）
    from clipwright.api.render import _preset_map

    assert "douyin" not in _preset_map(), "插件注销后 douyin 仍在预设表（有内置兜底？）"


@check("material_impl_in_plugin")
def check_material_impl_in_plugin() -> None:
    """B8-T8.7：`LocalFolderSource`（扫描/索引/搜索）必须只在 local_material 插件里。

    旧布局是 engine 持实现、插件只"读配置 + 实例化 + 注册"（S1+S2 半壳）：
    插件禁用后 engine 仍留着一个完整可用源，看起来能力还在。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins/local_material/local_folder.py"
    assert plugin.exists(), "缺少 plugins/local_material/local_folder.py"
    body = plugin.read_text(encoding="utf-8")
    for needle in ("class LocalFolderSource", "def refresh", "class _IndexedFile"):
        assert needle in body, f"插件实现缺少 {needle}"

    engine = root / "clipwright/material"
    assert not (engine / "local_source.py").exists(), "engine 仍有 material/local_source.py"
    joined = "".join(f.read_text(encoding="utf-8") for f in engine.rglob("*.py"))
    for needle in ("LocalFolderSource", "source_from_env"):
        assert needle not in joined, f"engine material 仍引用 {needle}"

    import clipwright.material as material

    assert not hasattr(material, "LocalFolderSource"), "material 包仍导出 LocalFolderSource"


@check("prompt_builder_guards")
def check_builder_guards() -> None:
    from clipwright.plugins import prompt_overrides as O

    # 1) 静态纯度：import / eval / getattr / async 一律拒绝
    for src in (
        "import os\ndef f(a):\n    return a\n",
        "def f(a):\n    return eval('1')\n",
        "def f(a):\n    return getattr(a, 'x')\n",
        "async def f(a):\n    return a\n",
    ):
        assert O.check_builder_source(src), f"静态检查漏放: {src[:24]}"

    # 2) 双 dry-render：参数声明了却不用 → 拒绝
    def dead(remaining: int, max_turns: int, used: int) -> str:
        return "固定文本"

    problems = O.register_builder_override("_verify", "build_budget_note", dead)
    assert problems, "参数不用的 builder 应被拒"
    assert any("输出相同" in p for p in problems), problems
    O.clear_plugin("_verify")

    # 3) 未知符号：拒绝
    assert O.register_constant_override("_verify", "NOT_A_SYMBOL", "x"), "未知符号应被拒"

    # 4) 引入默认值里不存在的新占位符：拒绝
    assert O.register_constant_override("_verify", "LOOP_BUDGET_HEADER", "{unknown}"), "新占位符应被拒"
    O.clear_plugin("_verify")


def _builtin_dub_skill() -> Any | None:
    """内置 `dub_script` 技能类；B3-T6 删掉 `skill/dub.py` 之后返回 None。"""
    try:
        from clipwright.skill.dub import DubScriptSkill
    except Exception:  # noqa: BLE001 — 删除后 ImportError 是预期状态，不是缺陷
        return None
    return DubScriptSkill


@check("dub_capability_boundary")
def check_dub_boundary() -> None:
    """配音能力：由插件提供；两边都摘掉后必须 503（薄转发不得兜底）。

    **B3-T6 兼容**：删内置之前，回收插件技能会从影子栈里**恢复内置**，得再摘一次才真空；
    删除之后一次到底。两种状态都必须过，这个脚本才能跨"删除"这一步继续当门禁用。

    收尾会把内置实现重新登记回去——**检查之间共享进程状态**，不恢复会让后面的检查
    看到「dub_script 不存在」而误报（本轮踩过）。
    """
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.skill.registry import SkillRegistry

    builtin_cls = _builtin_dub_skill()

    with TestClient(app, raise_server_exceptions=False) as c:
        owner = SkillRegistry.owner("dub_script")
        assert owner == "tts_feature", f"dub_script 归属应为 tts_feature，实际 {owner!r}"

        SkillRegistry.unregister("dub_script")
        if builtin_cls is not None:
            assert SkillRegistry.get("dub_script") is not None, "回收插件技能后内置应恢复（B3-T6 之前）"
            SkillRegistry.unregister("dub_script")
        assert SkillRegistry.get("dub_script") is None, "两次回收后不应还有 dub_script"
        r = c.post("/api/voice/dub", json={"voice_id": "x", "text": "y"})
        assert r.status_code == 503, f"无配音能力时应 503，实际 {r.status_code}"

    # 收尾：内置还在就登记回来（删除后没有可恢复的实现，属预期）
    if builtin_cls is not None:
        SkillRegistry.register(builtin_cls())
        assert SkillRegistry.get("dub_script") is not None, "收尾恢复内置失败"


@check("provider_plugin_fallback")
def check_provider_fallback() -> None:
    """插件被卸载后 **没有内置补位**——能力缺失是一等状态（2026-11-08 口径变更）。

    旧语义是"让位 + 懒注册补位"（禁用插件后 engine 顶上内置实现）。审计整改 B7 之后
    这条被明确推翻：实现归插件，禁用即没有该能力，调用方按"未安装"报可读错误。
    """
    from clipwright.plugins.contribution import ContributionKind as K
    from clipwright.plugins.contribution import ContributionRegistry as R
    from plugins.llm_openai_compatible.main import OpenACompatibleProviderPlugin

    saved = [c for c in OpenACompatibleProviderPlugin().contributions()]
    owner = "llm_openai_compatible"
    try:
        # 卸载 openai 兼容插件后，两个 id 应从门面消失
        R.unregister_plugin(owner)
        remaining = {c.id for _, c in R.list(K.LLM_PROVIDER)}
        assert "openai" not in remaining, "卸载后 openai 仍在门面"
        assert "ollama" not in remaining, "卸载后 ollama 仍在门面"

        from clipwright.services.llm import _resolve_llm_provider

        impl = _resolve_llm_provider("openai")
        assert impl is None, "禁用插件后不应再解析到内置实现（补位已删除）"
    finally:
        # 恢复插件贡献，避免影响后续检查
        R.declared_scope(owner, [c.key for c in saved])
        report = R.register(owner, saved)
        assert report.ok, report.rejected


@check("skill_shadow_restore")
def check_skill_shadow_restore() -> None:
    """插件技能被回收后，内置同名技能必须恢复（`_shadowed`），不能直接消失。

    **用 `analyze_video_structure` 而不是 `dub_script`**：后者正是 B3-T6 要删掉内置的那个，
    拿它当铁证会让脚本在删除后必然变红（而删除是**预期行为**）。影子栈机制与具体技能无关，
    换成一个确定会长期存在的内置技能，这条检查就能跨"删除"继续有效。

    **自带前置**：检查之间共享进程状态，这里自己把"插件覆盖"造出来再回收，
    不依赖别的检查留下的状态（否则顺序一变就误报）。
    """
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.skill.builtin import AnalyzeVideoStructureSkill
    from clipwright.skill.registry import SkillRegistry

    name = AnalyzeVideoStructureSkill.name

    # 必须在 app 启动后做：插件与内置技能都在 lifespan 里注册（单独跑本检查时尤其重要）
    with TestClient(app, raise_server_exceptions=False):
        assert SkillRegistry.get(name) is not None, f"前置：内置 {name} 应已注册"
        assert SkillRegistry.owner(name) == "", (
            f"前置：{name} 归属应为内置，实际 {SkillRegistry.owner(name)!r}"
        )

        SkillRegistry.register(AnalyzeVideoStructureSkill(), plugin_id="verify_shadow", allow_override=True)
        assert SkillRegistry.owner(name) == "verify_shadow", "前置失败：覆盖没生效"

        SkillRegistry.unregister(name)
        restored = SkillRegistry.get(name)
        assert restored is not None, "回收插件技能后内置没有恢复"
        assert type(restored).__name__ == "AnalyzeVideoStructureSkill", (
            f"恢复的不是内置实现：{type(restored).__name__}"
        )
        assert SkillRegistry.owner(name) == "", "恢复后归属应为内置（空串）"

def _reregister_plugin(plugin_id: str, module_path: str, class_name: str) -> None:
    """按 loader 的方式把某个插件的贡献重新注册回门面（用于检查后恢复状态）。"""
    import importlib

    from clipwright.plugins.contribution import ConflictPolicy, ContributionRegistry as R

    module = importlib.import_module(module_path)
    plugin = getattr(module, class_name)()
    items = list(plugin.contributions() or [])
    declared = {f"{it.kind.value}:{it.id}" for it in items}
    R.declared_scope(plugin_id, declared)
    report = R.register(plugin_id, items, ConflictPolicy.OVERRIDE)
    assert not report.rejected, f"恢复插件 {plugin_id} 失败: {[r[1] for r in report.rejected]}"


@check("all_families_fallback")
def check_all_families_fallback() -> None:
    """每一类 provider 在插件卸载后都必须能由内置补位（五类逐个验证）。

    只测 llm 是不够的：embedding / rerank / stt / tts 各自有独立的 `ensure_*`，
    某个家族漏了「懒补位」就会表现为「禁用插件 = 永久失去该类 provider」。

    **B3-T6 例外（TTS 退场）**：删掉 `ensure_tts_providers_registered()` 之后，TTS 不再是
    「让位」家族而是「退场」家族——卸载插件就该**没有** TTS provider。本检查按"模块里还
    有没有那个 ensure 函数"自动切换期望，所以删除前后都成立（删除是预期行为，不该把它
    变成红灯）。

    检查结束会把插件重新注册回去（否则后续检查看到的是 builtin 归属）。
    """
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.plugins.contribution import ContributionKind as K
    from clipwright.plugins.contribution import ContributionRegistry as R

    families = [
        (K.LLM_PROVIDER, "clipwright.providers.llm", "ensure_llm_providers_registered",
         "llm_openai_compatible", "plugins.llm_openai_compatible.main", "OpenACompatibleProviderPlugin",
         {"openai", "ollama"}),
        (K.TTS_PROVIDER, "clipwright.providers.tts", "ensure_tts_providers_registered",
         "tts_qwen", "plugins.tts_qwen.main", "QwenTtsProviderPlugin", {"qwen-tts"}),
        (K.EMBEDDING_PROVIDER, "clipwright.providers.embedding", "ensure_embedding_providers_registered",
         "embedding_providers", "plugins.embedding_providers.main", "EmbeddingProvidersPlugin",
         {"openai", "ollama", "local"}),
        (K.RERANK_PROVIDER, "clipwright.providers.rerank", "ensure_rerank_providers_registered",
         "rerank_providers", "plugins.rerank_providers.main", "RerankProvidersPlugin",
         {"http", "local"}),
        (K.STT_PROVIDER, "clipwright.providers.stt", "ensure_stt_providers_registered",
         "stt_providers", "plugins.stt_providers.main", "SttProvidersPlugin",
         {"whisper", "faster-whisper"}),
    ]

    with TestClient(app, raise_server_exceptions=False):
        for kind, mod, fn_name, plugin_id, mod_path, cls, ids in families:
            # 只卸载该家族里属于这个插件的 id（不整插件卸载，避免连带影响别的家族）
            R.unregister_keys(plugin_id, [(kind, i) for i in ids])
            gone = ids & {c.id for _, c in R.list(kind)}
            assert not gone, f"{kind.value}: 卸载 {plugin_id} 后仍存在 {sorted(gone)}"

            module = __import__(mod, fromlist=[fn_name])
            ensure = getattr(module, fn_name, None)

            if ensure is None:
                # 退场家族（B3-T6 之后的 TTS）：engine 不再注册，卸载后必须保持为空
                still = ids & {c.id for _, c in R.list(kind)}
                assert not still, (
                    f"{kind.value}: {mod} 已无 {fn_name}，卸载后 id 却还在 {sorted(still)}"
                    "（说明还有别处在偷偷注册内置）"
                )
                _reregister_plugin(plugin_id, mod_path, cls)
                for cid in ids:
                    owner = R.owner_of(kind, cid)
                    assert owner == plugin_id, f"恢复失败: {kind.value}:{cid} 归属 {owner!r}"
                continue

            # 走真实补位路径
            ensure()

            back = ids & {c.id for _, c in R.list(kind)}
            assert back == ids, f"{kind.value}: 内置没有补位（缺 {sorted(ids - back)}）"
            for cid in ids:
                owner = R.owner_of(kind, cid)
                assert owner == "__builtin__", (
                    f"{kind.value}:{cid} 补位后归属应为 __builtin__，实际 {owner!r}"
                )

            # 恢复插件归属，避免污染后续检查
            _reregister_plugin(plugin_id, mod_path, cls)
            for cid in ids:
                owner = R.owner_of(kind, cid)
                assert owner == plugin_id, f"恢复失败: {kind.value}:{cid} 归属 {owner!r}"

@check("tier_map_empty")
def check_tier_map() -> None:
    """不变量 #12：`capability_matrix.TIER_MAP` 必须为空（有值即写死的旧接线）。"""
    from clipwright.services import capability_matrix

    assert not dict(getattr(capability_matrix, "TIER_MAP", {}) or {}), "TIER_MAP 必须保持为空"


# ── 运行器 ────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Provider 插件化与提示词覆盖自检")
    parser.add_argument("-k", "--filter", default="", help="只跑名字包含该子串的检查")
    parser.add_argument("--trace", action="store_true", help="失败时打印完整堆栈")
    args = parser.parse_args()

    checks = [v for k, v in sorted(globals().items())
              if k.startswith("check_") and callable(v) and hasattr(v, "_check_name")]
    for fn in checks:
        name: str = fn._check_name  # type: ignore[attr-defined]
        if args.filter and args.filter not in name:
            continue
        try:
            fn()
            RESULTS.append((name, True, ""))
            print(f"[PASS] {name}")
        except Exception as e:  # noqa: BLE001 — 自检脚本要报告而不是崩
            detail = f"{type(e).__name__}: {e}"
            RESULTS.append((name, False, detail))
            print(f"[FAIL] {name}: {detail}")
            if args.trace:
                traceback.print_exc()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print()
    print(f"共 {total} 项，通过 {total - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())







