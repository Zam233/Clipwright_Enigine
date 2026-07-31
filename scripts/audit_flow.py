"""验证简报/规划书/persona/类型 流转到 Pipeline 各 agent 输入。"""
import os
import sys

os.chdir(r"D:\Clipweight")
sys.path.insert(0, r"D:\Clipweight")

from clipwright.schema.agent import (
    AgentContext, StructureInput, MaterialInput, EditInput,
    AnimationInput, AudioInput, QualityInput,
)
from clipwright.animation.mg.generator import MGGenerator
from clipwright.schema.timeline import Timeline, Track, ClipKind

tl = Timeline(id="tl_a", width=1920, height=1080, fps=30, duration_sec=815, tracks=[])

brief = {
    "title": "文理之争测试简报",
    "material_requirements": {"type": "MG动画+实拍", "source": "Pexels", "preference": "高对比度红色强调"},
    "animation_style": {"style": "硬切", "tone": "白底黑字红强调", "fonts": {"title": "思源黑体"}, "icons": "极简线条"},
    "asset_ratio": {"footage": "30%", "mg": "70%"},
    "bgm_requirement": "极简低频脉冲，心跳级压迫感",
    "special_requirements": ["拉康镜像可视化", "结尾留白"],
}
plan = {"total_duration_sec": 815, "scene_count": 6, "raw_scenes": [{"title": "s1"}]}

ctx = AgentContext(
    pipeline_id="audit_v2",
    persona_id="zam_knowledge_critical",
    category_plugin_id="knowledge_longform",
    topic="文理之争",
    extra_params={
        "script_text": "测试文稿",
        "audio_duration_sec": 815,
        "creative_brief": brief,
        "production_plan": plan,
    },
)

print("=" * 60)
print("1. StructureInput")
s = StructureInput(context=ctx, creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(s.creative_brief), "| production_plan:", bool(s.production_plan))

print("=" * 60)
print("2. MaterialInput")
m = MaterialInput(context=ctx, script_skeleton={}, creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(m.creative_brief), "| production_plan:", bool(m.production_plan))
mat = m.creative_brief.get("material_requirements", {})
print("   素材偏好提取:", "；".join(str(v) for k, v in mat.items() if v)[:80])

print("=" * 60)
print("3. EditInput")
e = EditInput(context=ctx, script_skeleton={}, candidate_clips=[], creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(e.creative_brief), "| production_plan:", bool(e.production_plan))
print("   规划书总时长:", e.production_plan.get("total_duration_sec"))

print("=" * 60)
print("4. AnimationInput")
a = AnimationInput(context=ctx, timeline=tl, creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(a.creative_brief), "| production_plan:", bool(a.production_plan))
print("   简报动画风格:", a.creative_brief.get("animation_style"))

print("=" * 60)
print("5. AudioInput")
au = AudioInput(context=ctx, timeline=tl, creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(au.creative_brief), "| production_plan:", bool(au.production_plan))
print("   简报 BGM:", str(au.creative_brief.get("bgm_requirement"))[:60])

print("=" * 60)
print("6. QualityInput")
q = QualityInput(context=ctx, timeline=tl, creative_brief=brief, production_plan=plan)
print("   creative_brief:", bool(q.creative_brief), "| production_plan:", bool(q.production_plan))
print("   简报特殊要求:", q.creative_brief.get("special_requirements"))

print("=" * 60)
print("7. MGGenerator 上下文注入（Persona + 类型 + 简报）")
cat_ctx = {
    "plugin_id": "knowledge_longform",
    "display_name": "知识区长片",
    "description": "高密度信息",
    "mg_style_guidance": "优雅学术",
    "brief_animation_style": brief.get("animation_style"),
    "brief_asset_ratio": brief.get("asset_ratio"),
}
section = MGGenerator._build_context_section(
    {"primary_color": "#12121a", "accent_color": "#dc1414"},
    cat_ctx,
)
print(section)
print("\n=== 全部流转验证完成 ===")
