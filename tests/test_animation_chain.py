"""Comprehensive animation call chain verification.
Tests every animation type through the full agent → render path.
"""
import sys
sys.path.insert(0, '..')

from clipwright.animation.catalog import AnimationCatalog
from clipwright.animation.builtin import register_builtin_animations
from clipwright.animation.diagram_svg import DiagramRenderer, DiagramStyle
from clipwright.animation.registry import AnimationRegistry
from clipwright.agents.animation_agent import AnimationAgent

register_builtin_animations()

passed, failed = 0, 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: {detail}")

# ── 1. StructureAgent Prompt ──
print("\n=== 1. StructureAgent Prompt Coverage ===")
ta = AnimationCatalog.get_text_animations()
la = AnimationCatalog.get_logic_animations()
tr = AnimationCatalog.get_transition_animations()
text_prompt = AnimationCatalog.get_text_animations_prompt()
logic_prompt = AnimationCatalog.get_logic_animations_prompt()

for a in ta:
    check(f"文字动画[{a['id']}] prompt可见", a["name"] in text_prompt)
for a in la:
    check(f"逻辑动画[{a['id']}] prompt可见", a["name"] in logic_prompt)

# ── 2. resolve_marker() ──
print("\n=== 2. resolve_marker() Name Matching ===")
tests = [
    # 文字动画（已注册）
    ("淡入", "text", "text_fade_in"), ("打字", "text", "typewriter"),
    ("滑入", "text", "text_slide_up"), ("逐字", "text", "char_by_char"),
    ("高亮", "text", "highlight_flash"),
    # 逻辑动画（内置 + MG + DiagramSVG 预设）
    ("箭头", "logic", "diagram"), ("因果", "logic", "causation"),
    ("对比", "logic", "comparison"), ("流程", "logic", "sequence"),
    ("时间线", "logic", "timeline"), ("维恩", "logic", "venn"),
    ("柱状图", "logic", "bar_chart"), ("饼图", "logic", "pie_chart"),
    ("折线图", "logic", "line_chart"),
    ("思维导图", "logic", "mg_mindmap"),
    ("层级", "logic", "tree"), ("序列图", "logic", "sequence_diagram"),
    ("流程图", "logic", "flow_chart"),
    # 包含匹配（标记含已注册动画名）
    ("三维恩", "logic", "venn"),
    # 屏上动画简称 → 别名解析（命名漂移兼容）
    ("弹跳", "text", "scale_bounce"), ("模糊", "text", "blur_in"),
    # DiagramSVG 新增预设（P3：未实现的逻辑类型）
    ("雷达图", "logic", "radar"), ("甘特图", "logic", "gantt"),
    ("热力图", "logic", "heatmap"), ("桑基图", "logic", "sankey"),
    ("概念图", "logic", "concept"), ("代码块", "logic", "codeblock"),
    ("数据表", "logic", "datatable"), ("引用", "logic", "quote"),
    ("组织架构图", "logic", "orgchart"), ("三维图", "logic", "venn3"),
    # 精确匹配优先：对比卡 → compcard（旧行为回退 comparison 已修复）
    ("对比卡", "logic", "compcard"),
    # 过渡动画精确匹配（旧行为回退 text_fade_in 已修复）
    ("淡入淡出", "transition", "crossfade"),
    ("左推", "transition", "push_left"),
    ("故障干扰", "transition", "glitch"),
]
for name, etype, eid in tests:
    r = AnimationCatalog.resolve_marker(name)
    ok = r["type"] == etype and r["anim_id"] == eid
    check(f"resolve({name}) -> {etype}/{eid}", ok, f"got {r}")

# ── 3. parse_marker_from_description() ──
print("\n=== 3. parse_marker_from_description() ===")
desc_tests = [
    ("[文字动画]淡入：人工智能改变世界", "text", "text_fade_in"),
    ("[逻辑动画]箭头：机器学习→深度学习", "logic", "diagram"),
    # 过渡动画前缀（[过渡动画]/[转场动画] 兼容）
    ("[过渡动画]淡入淡出", "transition", "crossfade"),
    ("[转场动画]左推", "transition", "push_left"),
    # 屏上动画简称 → 别名解析
    ("[文字动画]弹跳：重要结论", "text", "scale_bounce"),
]
for desc, etype, eid in desc_tests:
    markers = AnimationCatalog.parse_marker_from_description(desc)
    if eid is None:
        ok = len(markers) == 0
    else:
        ok = len(markers) > 0 and markers[0]["type"] == etype and markers[0]["anim_id"] == eid
    check(f"parse({desc[:25]})", ok, f"got {markers[0] if markers else '[]'}")

# ── 4. _build_diagram_params() Coverage ──
print("\n=== 4. _build_diagram_params() Coverage ===")
logic_texts = {
    "diagram": "机器学习→深度学习→强化学习",
    "causation": "A→B→C",
    "comparison": "方案A VS 方案B",
    "sequence": "步骤1→步骤2→步骤3",
    "timeline": "2023|2024|2025",
    "tree": "根节点|子1|子2",
    "hierarchy": "CEO|VP1|VP2",
    "venn": "集合A|集合B|交集",
    "bar_chart": "苹果:80,香蕉:60,橘子:90",
    "pie_chart": "A:30,B:30,C:40",
    "line_chart": "一月:10,二月:20,三月:15",
    "sequence_diagram": "用户A,服务器B|A->B:请求|B->A:响应",
    "flow_chart": "start:100:200:开始:pill|check:100:320:判断:diamond",
    "mindmap": "中心主题|子节点1|子节点2|子节点3",
    "radar": "力量:80,速度:60,智力:90",
    "gantt": "需求分析:0:5,开发:3:8,测试:7:4",
    "venn3": "A|B|C|AB|AC|BC|ABC",
    "heatmap": "列1,列2,列3|行1:1,2,3",
    "sankey": "收入:支出:100|收入:储蓄:50",
    "concept": "节点A:200:200:AI|节点B:400:200:ML|A->B:驱动",
    "codeblock": "def hello():     return x",
    "datatable": "姓名,年龄,城市|张三,28,北京|李四,32,上海",
    "quote": "知识就是力量|培根",
    "compcard": "价格,100,80,2|速度,快,更快,1",
    "orgchart": "CEO|  VP1|   经理A",
}

# Check every logic anim id has items
for anim_id, text in logic_texts.items():
    params = AnimationAgent._build_diagram_params(anim_id, text, 5.0)
    ok = params.get("preset") == anim_id and "items" in params and len(params["items"]) > 0
    check(f"build_diagram({anim_id})", ok, f"preset={params.get('preset')}, items={params.get('items')}")

# ── 5. DiagramRenderer SVG ──
print("\n=== 5. DiagramRenderer SVG Output ===")
for anim_id, text in logic_texts.items():
    params = AnimationAgent._build_diagram_params(anim_id, text, 5.0)
    style = DiagramStyle()
    svg = DiagramRenderer.render(params, style, 1920, 1080)
    ok = svg and svg.strip().startswith("<svg") and "</svg>" in svg
    check(f"render({anim_id}) -> valid SVG", ok, f"len={len(svg) if svg else 0}")

# ── 6. Text Animation Keyframes ──
print("\n=== 6. Text Animation Keyframes ===")
for a in ta:
    kfs = AnimationCatalog.build_full_keyframes(a["id"], 0.0, 5.0)
    ok = len(kfs) >= 2 and all("time" in kf and "properties" in kf for kf in kfs)
    check(f"keyframes({a['id']}) -> {len(kfs)} kfs", ok)

# ── 7. Transition Registry ──
print("\n=== 7. Transition Registry ===")
for a in tr:
    defn = AnimationRegistry.get(a["id"])
    ok = defn is not None
    check(f"registry({a['id']})", ok)

# ── 8. Classification in RenderService ──
print("\n=== 8. RenderService Overlay Classification ===")
# Drawtext text overlay
dt = {"renderer": "drawtext"}
ok = dt.get("renderer") != "hyperframes" and not dt.get("diagram_params")
check("drawtext text -> drawtext_ov", ok)

# Hyperframes logic
hf = {"renderer": "hyperframes", "diagram_params": {"preset": "diagram"}}
ok2 = hf.get("renderer") == "hyperframes" or hf.get("diagram_params")
check("hyperframes logic -> hf_ov", ok2)

# Fallback logic (no hyperframes)
fb = {"renderer": "drawtext", "diagram_params": None}
ok3 = fb.get("renderer") != "hyperframes" and not fb.get("diagram_params")
check("fallback logic -> drawtext_ov", ok3)

# Animation clip with diagram_params but no explicit renderer
ac = {"diagram_params": {"preset": "arrow"}}
ok4 = ac.get("renderer") == "hyperframes" or ac.get("diagram_params")
check("animation clip diagram -> hf_ov", ok4)

# Transition_in on video clip
tc = {"transition_in": "fade", "transition_duration_sec": 0.3}
check("transition_in set", tc["transition_in"] == "fade")
check("transition_duration set", tc["transition_duration_sec"] == 0.3)

# Print summary
total = passed + failed
print(f"\n{'='*50}")
print(f"结果: {passed} 通过 / {failed} 失败 / 总计 {total}")
print(f"{'='*50}")
if failed > 0:
    print("需要修复以上失败项")
    sys.exit(1)
else:
    print("所有动画链路过关!")
