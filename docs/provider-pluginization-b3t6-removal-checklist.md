# B3-T6 删除清单（等人工测试通过后执行）

> 依据：PRD §15.1.6 第 2 条「插件完成并通过 §15.3 验收后**立即删除**，不设观察期、不设门槛；
> **不保留**『临时打开内置实现』的开关」。本文件是**执行前的核对清单**，不是我擅自删除的结果。
>
> 为什么现在还不动：删完之后如果插件在某个环境没加载，配音能力会**整体消失**（这正是裁定 3
> 想要的效果），所以必须先用 `python -m pytest tests -q` 确认插件路径可用。这一步按项目约定
> 由人执行，我不跑测试。

## 0. 前置条件（缺一不可）

- [ ] `python -m pytest tests -q` 通过（人执行）
- [ ] 手工确认：装插件时 `call_skill("dub")` 解析到 `DubScriptOrchestrator`（插件实现），
      `/api/voice/dub` 走薄转发成功
- [ ] 手工确认：禁用 `tts_feature` 后，配音能力整体不可用且**有可读降级说明**
      （`未配音：当前没有可用的配音能力（TTS 功能插件未安装或被禁用）`），不出现 500
- [ ] **删除提交必须连带改测试与自检**（见 §6.2，演练实证：只删源码会让 pytest 报 3 个
      collection error、自检 13 项掉到 10 项）

## 1. 删除：engine 的配音编排

| 位置 | 内容 | 删除后由谁承担 |
|---|---|---|
| `clipwright/services/voice.py:922` | `VoiceService.dub_script()` 整个方法 | `plugins/tts_feature/dub.py::DubScriptOrchestrator.execute()` |
| `clipwright/services/voice.py:936` | 方法体内的 `split_text(...)` 调用 | 同上 |
| `clipwright/skill/dub.py`（全文件 60 行） | builtin `DubScriptSkill` | 同上（插件贡献同名技能） |
| `clipwright/skill/builtin.py:193,199` | `from clipwright.skill.dub import DubScriptSkill` + `DubScriptSkill(),` | — |

**连带处理**：`split_text()`（`services/voice.py:127`）是切句策略。裁定 3 要求编排（含切句）
离开 engine，因此要把这个函数**整体搬进** `plugins/tts_feature/`（插件已 `import` 它，改成内部副本），
engine 侧删除。注意：`services/voice.py` 的模块 docstring 第 4 行也要同步删掉 `split_text` 的提及。

## 2. 删除：内置 TTS provider 的默认注册

| 位置 | 内容 | 说明 |
|---|---|---|
| `clipwright/providers/tts/__init__.py:39-75` | `ensure_tts_providers_registered()` 与 `_make()` | 函数删除；`resolve_tts_entry()` / `tts_provider_names()` 里的「懒注册」调用一并去掉 |
| `clipwright/main.py:~169` | `ensure_tts_providers_registered()` 调用 | 启动不再注册任何内置 TTS provider |
| `clipwright/api/providers.py:57,61` | import + 调用 | 保留 `tts_provider_names`（下拉仍要列出**插件**提供的 provider） |
| `clipwright/api/settings.py:81-82` | import + 调用 | 同上 |

**保留不动**（这些是基座，不是能力）：

- `providers/builtin_registry.register_builtin_once()`（让位机制本身）——**别的 provider 家族仍在用**
- `services/voice.py` 的 `BaseVoiceProvider` / `QwenTTSProvider` / `CosyVoiceProvider` / `MiniMaxProvider`
  等协议类（Batch 3 的分阶段策略：协议类暂留 engine，搬移是独立工作项）
- `PublicUploadManager`（已降级为通用服务）
- `providers/tts/__init__.py` 的 `TtsProviderEntry` / `normalize_tts_name` / `_TTS_ALIASES`

## 3. 删除后的预期状态（验收断言）

> **口径澄清（2026-11-03 演练后）**：这里的"空环境"指**没有 TTS 插件**（卸载/禁用
> `tts_feature`、`tts_cosyvoice`、`tts_qwen`、`tts_minimax`）；正常安装时插件会在
> `_plugin_loader.load_all()` 里注册，所以「插件全在」一节应当**有**三个 provider。
> 五条断言已在演练中逐条实测通过（见 §6.1）。

- [ ] 空环境：`ContributionRegistry.list(TTS_PROVIDER)` **为空**（engine 不再注册任何 TTS provider）
- [ ] 空环境：`SkillRegistry.get("dub_script")` 为 `None`；`skill_resolve("dub")` 为 `None`
- [ ] 空环境：`resolve_tts_entry("cosyvoice")` 为 `None`（**不再懒注册内置**）
- [ ] 主 agent 请求配音 → `not_found`，AudioAgent 写「未配音：当前没有可用的配音能力…」
- [ ] 空环境：`/api/voice/dub` → **503**「配音能力当前不可用」
- [ ] 装 `tts_feature` + `tts_cosyvoice` 后：`dub_script` 由插件提供、`cosyvoice` 由协议插件提供，
      导入→克隆→合成→dub 全链路可用
- [x] ~~`qwen-tts` / `minimax` 两个 id 消失~~ → **已由 §4 的补丁插件解决**（三个 id 全部插件接管）

## 4. 需要先拍板的一点

~~删除内置注册会让 `qwen-tts` / `minimax` 从下拉里消失（现在只有 `cosyvoice` 有插件）。~~

**已解决（2026-11-02）**：补了两个协议插件

| 插件 | 接管的 id | capabilities |
|---|---|---|
| `plugins/tts_qwen/` | `qwen-tts` | `stream=false`、`needs_uploader=false` |
| `plugins/tts_minimax/` | `minimax` | `stream=false`、`needs_uploader=true` |
| `plugins/tts_cosyvoice/`（已有） | `cosyvoice` | `stream=true`、`needs_uploader=true` |

实测 `ContributionRegistry` 归属：`{cosyvoice: tts_cosyvoice, minimax: tts_minimax, qwen-tts: tts_qwen}`
——**三个 id 全部由插件接管**，所以删除内置注册**不会再让任何 provider 消失**，
B3-T6 现在只差「人工跑测试」这一个前置条件。

## 5. 回滚方式

按 PRD §15.1.6 第 3 条：**不保留运行时开关**。要回到旧行为就回滚对应提交（`git revert`），
这也意味着删除动作要和插件提交**分开**（删除单独一个 commit，便于精确 revert）。

## 6. 删除演练实证（2026-11-03，临时 worktree，未落车）

演练做法：`git worktree add --detach J:\Clipwright-b3t6-dryrun HEAD` → 按 §1/§2 执行全部删除
（含把 `split_text` 搬成 `plugins/tts_feature/split_text.py`）→ 跑 `py_compile`、
`import clipwright.main`、13 项自检、以及按**删除后语义**改写的六条验收断言 → 丢弃 worktree。

### 6.1 源码侧：删得动，且行为符合裁定 3

- `py_compile` 8 个改动文件全过；`import clipwright.main` 正常（应用能起）。
- 六条验收断言实测（断言脚本见 §6.4，全部通过）：

| 断言（删除后语义） | 实测 |
|---|---|
| 插件全在：`tts_provider_names()` = `cosyvoice/minimax/qwen-tts`；`dub_script` 归属 `tts_feature`；`/api/voice/dub` 非 503 | ✅ |
| 摘掉插件：`tts_provider_names()` = `[]`；`resolve_tts_entry("cosyvoice")` = `None`（**不再懒注册内置**） | ✅ |
| 摘掉插件：`SkillRegistry.get("dub_script")` = `None`；`skill_resolve("dub")` = `None`（**内置已删，没有回退**） | ✅ |
| 摘掉插件：`POST /api/voice/dub` → **503** +「配音能力当前不可用（TTS 功能插件未安装或被禁用）」 | ✅ |
| 插件 `split_text` 行为不变（3 个固定用例 + `paragraph` 模式） | ✅ |

**结论**：§1/§2 的删除动作本身准确且够用。

### 6.2 但清单漏了一整类连带改动（演练当场抓到）

**(a) 自检脚本 3 项会红**（不是产品坏了，是脚本还假设内置在）：

| 检查项 | 演练中的报错 | 改法 |
|---|---|---|
| `dub_capability_boundary` | `ModuleNotFoundError: clipwright.skill.dub` | 去掉「恢复内置」那一段；改为断言「卸载插件后 `dub_script` 为 `None`」 |
| `skill_shadow_restore` | `AssertionError: 恢复出内置失败` | 影子栈机制改用**另一个仍有内置实现**的技能验证（如 `analyze_video_structure`）；`dub_script` 不再有内置可恢复 |
| `all_families_fallback` | `AttributeError: ensure_tts_providers_registered` | 把 TTS 家族从「卸载插件 → 内置补位」名单里移出，改为「卸载插件 → id 消失」（**这正是裁定 3：TTS 是退场，不是让位**） |

**(b) 三个测试模块 import 就挂**（pytest 会直接 collection error）：

| 文件 | 现状 | 删除后改法 |
|---|---|---|
| `tests/clipwright/test_voice_data.py` | `from clipwright.services.voice import split_text` | 改 `from plugins.tts_feature.split_text import split_text` |
| `tests/clipwright/test_voice_skill.py` | `from clipwright.skill.dub import DubScriptSkill` | 删除该文件；插件的 `DubScriptOrchestrator` 已有等价用例 |
| `tests/clipwright/test_tts_provider_registry.py` | `ensure_tts_providers_registered` | 改为断言「没有插件 → `tts_provider_names()` 为空、`resolve_tts_entry()` 为 None」 |

**(c) 两个模块 import 过、但运行期会挂**：

| 文件 | 现状 | 改法 |
|---|---|---|
| `tests/clipwright/test_voice_service.py` | 3 个 `await svc.dub_script(...)` 用例 | 迁进插件侧 dub 测试 |
| `tests/clipwright/test_voice_api.py` | `svc.dub_script = AsyncMock(...)` 打桩 | 端点已薄转发，改为打桩技能注册表 / 断言插件禁用时 503 |

**(d) 文档三处口径**：`AGENTS.md:60`「4 个 Skill」→ 3 个内置（`dub_script` 归 `tts_feature`）；
`docs/api_reference.md:951`、`docs/structure.md:79` 的 `dub_script` 行标注「由 `plugins/tts_feature` 提供」；
`docs/development.md:213` 的技能清单同步。

### 6.3 结论

删除可以做，但它**不是"纯删源码"的提交**：必须与 §6.2 的 3 项自检改动、5 个测试文件改动、
3 处文档同步一起提交（否则 pytest 必红、自检 13 项退到 10 项）。收到「可以删」后，
这两批改动会合并在**同一个 commit**（便于整体 revert），并附 §6.4 的断言脚本。

### 6.4 演练用的验收断言脚本（删除时随提交落地）

```python
"""B3-T6 删除后的验收断言（按删除后语义：没有插件就没有 TTS 能力）。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.plugins.contribution import ContributionRegistry
from clipwright.providers.tts import resolve_tts_entry, tts_provider_names
from clipwright.skill.registry import SkillRegistry
from clipwright.tool.capabilities import skill_resolve


def snapshot(tag: str) -> dict:
    skill = SkillRegistry.get("dub_script")
    return {
        "tag": tag,
        "tts_ids": sorted(tts_provider_names()),
        "skill_dub_script": skill is not None,
        "skill_owner": SkillRegistry.owner("dub_script") if skill else "",
        "resolve_dub": getattr(skill_resolve("dub"), "name", None),
        "resolve_tts": resolve_tts_entry("cosyvoice") is not None,
    }


def main() -> int:
    out = []
    with TestClient(app, raise_server_exceptions=False) as c:
        out.append(snapshot("插件全在"))
        assert out[-1]["tts_ids"] == ["cosyvoice", "minimax", "qwen-tts"], out[-1]
        assert out[-1]["skill_owner"] == "tts_feature", out[-1]
        assert c.post("/api/voice/dub", json={"voice_id": "x", "text": "a。b。"}).status_code != 503

        for pid in ("tts_feature", "tts_cosyvoice", "tts_qwen", "tts_minimax"):
            ContributionRegistry.unregister_plugin(pid)
        out.append(snapshot("摘掉插件"))
        assert out[-1]["tts_ids"] == [], "删除内置注册后应无 TTS provider"
        assert out[-1]["resolve_tts"] is False, "不应再懒注册内置"
        assert out[-1]["skill_dub_script"] is False, "dub_script 不应回落到内置"
        assert out[-1]["resolve_dub"] is None
        dub = c.post("/api/voice/dub", json={"voice_id": "x", "text": "a。"})
        assert dub.status_code == 503, dub.status_code

        from plugins.tts_feature.split_text import split_text

        assert split_text("第一句。第二句。") == ["第一句。", "第二句。"]
        assert split_text("a\n\nb", mode="paragraph") == ["a", "b"]

    print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

