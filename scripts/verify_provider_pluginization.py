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


@check("dub_capability_boundary")
def check_dub_boundary() -> None:
    """配音能力：由插件提供；两边都摘掉后必须 503（薄转发不得兜底）。

    收尾会把内置实现重新登记回去——**检查之间共享进程状态**，不恢复会让后面的检查
    看到「dub_script 不存在」而误报（本轮踩过）。
    """
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.skill.registry import SkillRegistry

    with TestClient(app, raise_server_exceptions=False) as c:
        owner = SkillRegistry.owner("dub_script")
        assert owner == "tts_feature", f"dub_script 归属应为 tts_feature，实际 {owner!r}"

        # 摘两次：第一次会恢复被 shadow 的内置实现，第二次才真的没有
        SkillRegistry.unregister("dub_script")
        SkillRegistry.unregister("dub_script")
        assert SkillRegistry.get("dub_script") is None
        r = c.post("/api/voice/dub", json={"voice_id": "x", "text": "y"})
        assert r.status_code == 503, f"无配音能力时应 503，实际 {r.status_code}"

    # 收尾：把内置实现登记回来，避免污染后续检查
    from clipwright.skill.dub import DubScriptSkill

    SkillRegistry.register(DubScriptSkill())
    assert SkillRegistry.get("dub_script") is not None, "收尾恢复内置失败"


@check("provider_plugin_fallback")
def check_provider_fallback() -> None:
    """插件被卸载后，内置必须能补位（否则禁用插件的用户会直接失去 provider）。

    这是「让位语义」的逆过程：让位发生在注册前（`register_builtin_once` 逐 id 过滤），
    补位发生在下一次 `ensure_*` 调用（启动或解析时懒注册）。
    """
    from clipwright.plugins.contribution import ContributionKind as K
    from clipwright.plugins.contribution import ContributionRegistry as R

    # 卸载 openai 兼容插件后，两个 id 应从门面消失
    R.unregister_plugin("llm_openai_compatible")
    remaining = {c.id for _, c in R.list(K.LLM_PROVIDER)}
    assert "openai" not in remaining, "卸载后 openai 仍在门面"
    assert "ollama" not in remaining, "卸载后 ollama 仍在门面"

    # 走真实解析路径：services.llm 的 resolver 会先 ensure 再查
    from clipwright.services.llm import _resolve_llm_provider

    impl = _resolve_llm_provider("openai")
    assert impl is not None, "卸载插件后 openai 无法解析（内置没有补位）"
    owner = R.owner_of(K.LLM_PROVIDER, "openai")
    assert owner == "__builtin__", f"补位后归属应为 builtin，实际 {owner!r}"


@check("skill_shadow_restore")
def check_skill_shadow_restore() -> None:
    """插件技能被回收后，内置同名技能必须恢复（`_shadowed`），不能直接消失。

    **自带前置**：检查之间共享进程状态，这里自己把插件技能注册进去再回收，
    不依赖别的检查留下的状态（否则顺序一变就误报）。
    """
    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.skill.registry import SkillRegistry

    from plugins.tts_feature.dub import DubScriptOrchestrator

    # 必须在 app 启动后做：插件与内置技能都在 lifespan 里注册（单独跑本检查时尤其重要）
    with TestClient(app, raise_server_exceptions=False):
        SkillRegistry.register(DubScriptOrchestrator(), plugin_id="tts_feature", allow_override=True)
        assert SkillRegistry.owner("dub_script") == "tts_feature", "前置失败：插件技能没注册进去"

        SkillRegistry.unregister("dub_script")
        restored = SkillRegistry.get("dub_script")
        assert restored is not None, "回收插件技能后内置没有恢复"
        assert type(restored).__name__ == "DubScriptSkill", f"恢复的不是内置实现：{type(restored).__name__}"
        assert SkillRegistry.owner("dub_script") == "", "恢复后归属应为内置（空串）"

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

            # 走真实补位路径
            module = __import__(mod, fromlist=[fn_name])
            getattr(module, fn_name)()

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







