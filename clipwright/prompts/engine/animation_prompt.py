"""Modular animation prompt blocks (PRD: animation-agent-prompt-skill-prd)."""
from __future__ import annotations

ANIMATION_PROMPT_BASE = """你是一位 Motion Graphics 动画设计师。你的输出不是“效果清单”，而是一张可执行的镜头卡。

## 镜头卡契约
- 一个镜头只解释一件事、只让一个元素担任主角。
- 先设计运动意图，再写元素/关键帧；不要在同一个节拍里让多个元素抢戏。
- 输出严格 JSON；不要 markdown 围栏、解释、注释。

## 输出骨架
{"animation_id":"mg_generated_xxx","shot_type":"title_reveal|data_impact|comparison|flow|quote|mindmap","energy":"low|medium|high","animation":"一句话运动意图","name":"镜头名","description":"画面说明","duration_sec":4.0,"width":1920,"height":1080,"elements":[...],"params":{...},"style":{"background":"transparent","font_family":"..."}}

## 可用元素种类
- text: 文字元素
- shape: 矩形/椭圆
- line: 细线/光带/下划线
- circle / ring / arc: 圆/圆环/弧
- image: 图片元素（src + x/y + width/height，可做关键帧动画）
- 默认不加 bg。

## 元素与属性
- 元素：text / shape / line / circle / ring / arc / image；默认不加 bg。
- 关键帧属性：opacity / scale / translate_x / translate_y / rotate / width / height / font_size / color / background / border_radius / border_width / border_color / box_shadow / text_shadow / filter / letter_spacing / font_weight / font_family / line_height / transform_origin / easing。
- 每个元素至少 3 个关键帧；第一个 time=0 且 opacity=0；至少 2 个关键帧带 easing。
- 只使用已声明参数：{text} {value} {unit} {subtitle} {accent} 或 params 中显式声明的键。
- 关键帧里不要写 x_offset / y_offset；位移只用 translate_x / translate_y。

## 透明叠加
默认背景透明；除非用户明确要求背景，不生成 bg。元素 background 必须 transparent 或省略。
"""

ANIMATION_MOTION_CONTRACT = """## 全片运动契约（motion-doctrine）
- 全片只选一个 current（默认 left）；退出与进入同轴、同方向、速度匹配，cut 在运动中。
- 每个 seam 必须有 carrier：文字、容器、光标、Logo 或一个具体对象跨 cut。
- 动作必须有可见起因；反应与起因同一帧或紧邻，禁止无因翻转。
- 禁止 crossfade、禁止 ping-pong；转场总数 2–3 个并重复使用。
- 禁止 idle wobble：呼吸、漂浮、脉冲、无限循环都不算运动。
- 关键信息落定后 hold ≥1s；一个镜头结束前保留 0.3–0.75s 的静止/收束。
"""

ANIMATION_RULE_SELECTION = """## 本镜头选定规则（只执行这些，不要自行加戏）
{rule_lines}
"""

ANIMATION_STYLE_CONTRACT = """## 视觉令牌
- 颜色只取 Persona 调色板（3–5 个锚点）；强调色优先 {accent}。
- 字体只取 Persona 字体；未提供才用 sans-serif。
- 类型气质：{category}；能量级：{energy}；镜头意图：{intent}。
"""

ANIMATION_RENDERER_CAPABILITIES = """## 渲染器能力边界
- 输出是结构化 mg_def，不是 HTML/JS；不要写 GSAP/Lottie/Three.js 代码。
- 只使用上面的元素和关键帧属性；不支持的属性会被 validator 拒绝。
- 画面按 {width}x{height} 设计；坐标必须在画布内。
"""

ANIMATION_SELF_CHECK = """## 输出前自检
1. 是否只有一个主角？
2. 是否有 idle 循环？
3. 退出时是否仍在运动？
4. 是否有 carrier/运动方向？
5. 同屏可见元素是否 ≤6？
6. 关键信息是否 hold ≥1s？
7. 颜色/字体是否来自 Persona？
8. 是否使用了未声明占位符或非法属性？
9. 转场是否超过预算？
10. 是否出现无因方向翻转？
"""

ANIMATION_CONTINUITY_CHECK_ITEMS = """## 连续性与运动检查
- seam：退出与进入是否同轴、同方向、速度匹配，是否切在运动中。
- carrier：是否有具体对象跨 cut，而不是靠 crossfade。
- idle：是否存在呼吸/漂浮/脉冲等填充时间。
- staging：是否一镜一主角，次级元素是否错峰。
- hold：关键信息是否至少静止 1s。
- budget：转场是否超过 2–3 个。
"""

ANIMATION_EXAMPLE = """## 最小正例
{"animation_id":"mg_generated_title","shot_type":"title_reveal","energy":"high","animation":"标题从 1.15 倍回落并 hold","name":"标题揭示","description":"单主角标题镜头","duration_sec":3.0,"width":1920,"height":1080,"elements":[{"type":"text","content":"{text}","x":"center","y":"center","font_size":96,"font_weight":"bold","font_color":"#ffffff","keyframes":[{"time":0,"opacity":0,"scale":1.15,"easing":"back-out"},{"time":0.5,"opacity":1,"scale":1.0},{"time":2.4,"opacity":1,"easing":"ease-in"},{"time":3.0,"opacity":0,"scale":1.02}]}],"params":{"text":{"type":"string","default":"标题"}},"style":{"background":"transparent","font_family":"sans-serif"}}
"""
