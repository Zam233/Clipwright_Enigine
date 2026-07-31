"""审计脚本：检测 Persona / 视频类型 / 插件提示词 / 插件工具 的注入链路。

用法: python scripts/audit_injection.py
输出: 各注入点的运行态数据 + 代码引用确认。
"""

import os
import sys

os.chdir(r"D:\Clipweight")
sys.path.insert(0, r"D:\Clipweight")

import json


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    # ── 0. 初始化（与应用启动等价） ──
    from clipwright.tool import register_builtin_tools
    register_builtin_tools()

    from clipwright.main import _register_builtin_plugins
    _register_builtin_plugins()

    from clipwright.plugins import PluginLoader
    from clipwright.config import settings
    loader = PluginLoader(plugin_dir=settings.plugin_dir)
    loader.load_all()

    from clipwright.animation.mg import register_agent_prompts
    register_agent_prompts()

    from clipwright.category.registry import CategoryRegistry
    from clipwright.persona.loader import load_persona_by_id, resolve_inheritance

    # ── 1. Persona 数据加载 ──
    section("1. Persona 数据加载 (zam_knowledge_critical)")
    manifest = load_persona_by_id("zam_knowledge_critical")
    manifest = resolve_inheritance(manifest)
    persona_config = manifest.parameter.model_dump(mode="json") if manifest.parameter else {}
    identity = manifest.parameter.identity.model_dump(mode="json") if manifest.parameter and manifest.parameter.identity else {}
    visual = persona_config.get("visual", {})
    prompt_md = getattr(manifest, "prompt", "") or ""

    print(f"Persona ID: {manifest.persona_id}")
    print(f"name: {getattr(manifest, 'persona_name', '')}")
    print(f"description: {getattr(manifest, 'description', '')}")
    print(f"parameter 顶层字段: {list(persona_config.keys())}")
    print(f"identity 字段: {list(identity.keys())}")
    print(f"visual 字段: {list(visual.keys())}")
    if visual:
        for k, v in visual.items():
            print(f"  visual.{k} = {str(v)[:80]}")
    print(f"prompt.md 长度: {len(prompt_md)}")
    print(f"prompt.md 预览: {prompt_md[:100]!r}")

    # 检查所有 persona 的 prompt.md 情况
    import glob, pathlib
    print("\n各 Persona prompt.md 情况:")
    for dirpath in sorted(glob.glob(r"D:\Clipweight\personas\*")):
        pid = pathlib.Path(dirpath).name
        pmd = pathlib.Path(dirpath) / "prompt.md"
        has = pmd.exists()
        ln = len(pmd.read_text(encoding="utf-8")) if has else 0
        print(f"  {pid}: prompt.md {'存在' if has else '缺失'} (len={ln})")

    # ── 2. pipeline_v2._init 模拟：构建 AgentContext / 输入 ──
    section("2. 管线层注入 (pipeline_v2._init 等价)")
    category_id = "knowledge_longform"
    plugin = CategoryRegistry.get(category_id)
    print(f"category: {category_id} → {plugin.display_name}")
    translated = plugin.translate_persona(manifest.parameter) if manifest.parameter else {}
    print(f"translate_persona 输出字段: {list(translated.keys())}")

    from clipwright.schema.agent import AgentContext
    agent_context = AgentContext(
        pipeline_id="audit_001",
        persona_id="zam_knowledge_critical",
        category_plugin_id=category_id,
        topic="文理之争",
        extra_params={
            **translated,
            "_persona_config": persona_config,
            "_identity": identity,
            "script_text": "测试文稿",
            "video_mode": "voiceover",
        },
    )
    print("AgentContext.extra_params 键:", list(agent_context.extra_params.keys()))
    print("AgentContext.category_plugin_id:", agent_context.category_plugin_id)
    print("AgentContext.persona_id:", agent_context.persona_id)

    # ── 3. StructureAgent 注入审计 ──
    section("3. StructureAgent 注入审计")
    from clipwright.agents.structure_agent import StructureAgent, SYSTEM_PROMPT_TPL, TOOL_PROMPT
    from clipwright.schema.agent import StructureInput

    tone = persona_config.get("tone", {})
    print("tone 参数:", json.dumps(tone, ensure_ascii=False)[:200])
    acad = persona_config.get("academic_density", "medium")
    max_len = persona_config.get("max_sentence_len", 20)
    cut = persona_config.get("cut_profile", "balanced")
    max_dur = 700
    sys_prompt = SYSTEM_PROMPT_TPL.format(
        tone=tone, academic_density=acad, max_sentence_len=max_len,
        cut_profile=cut, max_duration=max_dur,
    ) + TOOL_PROMPT
    if prompt_md:
        sys_prompt += "\n\n## Persona 风格指引\n" + prompt_md

    # 插件提示词注入（structure agent 槽位）
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    plugin_prompts = PluginPromptRegistry.get_for_agent("structure")
    if plugin_prompts:
        sys_prompt += "\n\n## 插件能力\n" + "\n\n".join(plugin_prompts)

    print(f"SYSTEM_PROMPT_TPL 引用 persona 字段: tone={tone!r:.50}, academic={acad}, max_sentence_len={max_len}, cut_profile={cut}")
    print(f"persona_prompt 注入 system_prompt: {len(prompt_md) > 0}")
    print(f"插件提示词注入数量 (structure): {len(plugin_prompts)}")
    for p in plugin_prompts:
        print(f"  - {p[:60].replace(chr(10), ' ')}...")
    print(f"system_prompt 总长度: {len(sys_prompt)}")

    # StructureAgent 工具列表（代码硬编码）
    import inspect
    src = inspect.getsource(StructureAgent.execute)
    import re
    m = re.search(r'tool_names\s*=\s*(\[[^\]]*\])', src)
    print(f"StructureAgent tool_names (LLM tool-use): {m.group(1) if m else '未找到'}")

    # ── 4. AnimationAgent + MGGenerator 注入审计 ──
    section("4. AnimationAgent + LLM MG 注入审计")
    from clipwright.agents.animation_agent import AnimationAgent
    from clipwright.animation.mg.generator import MGGenerator
    from clipwright.services.style_interpreter import StyleInterpreter

    # 模拟 _resolve_style 的输入（不跑 LLM：用精确字段路径）
    vc = visual
    has_exact = any(vc.get(k) for k in ("primary_color", "secondary_color", "font_size"))
    print(f"visual_config 有精确颜色字段: {has_exact}")
    print(f"StyleInterpreter 注册插件: {StyleInterpreter._plugin is not None}")

    # MGGenerator 上下文构建（直接调用静态方法验证数据转换）
    cat_ctx = {
        "plugin_id": plugin.plugin_id,
        "display_name": plugin.display_name,
        "description": plugin.description,
        "shot_params": plugin.get_shot_params({}),
        "pacing": plugin.get_pacing() if hasattr(plugin, "get_pacing") else {},
        "mg_style_guidance": plugin.get_mg_style_guidance() if hasattr(plugin, "get_mg_style_guidance") else "",
    }
    persona_style = {
        "primary_color": vc.get("primary_color", ""), "secondary_color": vc.get("secondary_color", ""),
        "accent_color": vc.get("accent_color", ""), "font_size": vc.get("font_size", ""),
        "style_description": vc.get("style_description", ""), "palette": vc.get("palette", ""),
    }
    section_text = MGGenerator._build_context_section(persona_style, cat_ctx)
    print(f"AnimationAgent category_context 键: {list(cat_ctx.keys())}")
    print(f"MGGenerator _build_context_section 输出长度: {len(section_text)}")
    print(f"--- 输出预览 ---")
    print(section_text[:500])

    # ── 5. 插件提示词注册清单 ──
    section("5. PluginPromptRegistry 全部注册")
    registered = PluginPromptRegistry.list_registered()
    for agent_name, entries in registered.items():
        print(f"\n[agent={agent_name}] {len(entries)} 条")
        for e in entries:
            print(f"  plugin={e['plugin_id']} priority={e['priority']} desc={e['description']} len={len(e['prompt_preview'])}")

    # 各 agent 是否读取插件提示词（代码引用）
    import clipwright.agents.requirements_agent as req_mod
    import clipwright.agents.material_agent as mat_mod
    import clipwright.agents.structure_agent as str_mod
    import clipwright.agents.animation_agent as anim_mod
    for name, mod in [("requirements", req_mod), ("material", mat_mod), ("structure", str_mod), ("animation", anim_mod)]:
        src2 = inspect.getsource(mod)
        uses = "PluginPromptRegistry.get_for_agent" in src2
        print(f"agent={name}: 读取插件提示词 = {uses}")

    # ── 6. 插件工具注册与 Agent 可调用性 ──
    section("6. 插件工具注册 + Agent 可调用性")
    from clipwright.tool.registry import ToolRegistry
    plugin_tools = []
    for name, tool in ToolRegistry._tools.items():
        pid = getattr(tool, "_plugin_id", "")
        if pid:
            plugin_tools.append((name, pid))
    print(f"插件注册的工具 ({len(plugin_tools)}):")
    for name, pid in plugin_tools:
        print(f"  {name} ← {pid}")

    print("\n各 Agent 工具获取方式（代码审计）:")
    print("  StructureAgent:  LLM tool-use → tool_names 硬编码列表（见上）")
    print("  AnimationAgent:  ToolRegistry.execute() 直接调用（video_trim/TransitionApply 等）")
    print("  MaterialAgent:   MaterialRegistry（素材源插件）")
    print("  AudioAgent:      ToolRegistry.execute() + SkillRegistry")
    print("  EditAgent:       ToolRegistry.execute()")

    # 插件工具是否在 ToolRegistry 全局可用
    for name, pid in plugin_tools:
        t = ToolRegistry.get(name)
        print(f"  插件工具 {name} 全局可调用: {t is not None and t.is_available()}")

    # 内置工具的插件归属一览（确认哪些 Agent 编排路径可触及插件工具）
    print(f"\nToolRegistry 工具总数: {len(ToolRegistry._tools)}")
    print(f"内置工具: {len(ToolRegistry._tools) - len(plugin_tools)}")


if __name__ == "__main__":
    main()
