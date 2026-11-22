"""mg prompts — 从 prompts.py 按域拆出。"""
from __future__ import annotations

MG_STRICT_JSON_OUTPUT = '\n\n## 输出硬性要求\n- 只输出一个合法的 JSON 对象，不要包含任何解释性文字、前言或后语。\n- 不要使用 markdown 代码块围栏（``` 或 ```json），直接输出原始 JSON。\n- 输出中除 JSON 外不要出现任何其他字符。\n'

MG_NO_BACKGROUND_CONSTRAINT = '背景规则（PRD §17 D7）：默认透明背景；动画叠加实拍素材时禁止不透明全幅底色/整屏遮挡，可用 rgba(0,0,0,0.25–0.55) 半透明底板（shape 或 bg，高≤画面45%）。实拍叠加场景的文字需 text_shadow/text_stroke/底板之一；纯色/暗底场景允许干净文字。'

MG_ORCHESTRATION_CONSTRAINTS = '\n\n## 动画编排约束\n- 顺序揭示（sequential reveal）：元素按阅读顺序逐个出现，重要数据点停顿 1-2 秒，避免多个元素同时动画。\n- 每节拍一个主运动（one primary motion per beat）：同一时刻只突出一个主运动，次级元素错峰（stagger）延迟入场。\n- 方向强化语义（direction reinforces semantics）：动画方向强化信息语义，增长向上、下降向下；流程类动画自左向右推进。\n- 构图平衡（balanced composition）：整体构图平衡，但按风格决定左对齐/居中；不要默认所有标题都居中。\n- 连线止于节点边缘（connectors stop at node edge）：连接线/箭头终止于节点边缘，不得穿入节点内部。\n- 箭头方向随流程（arrow direction follows flow）：流程 L→R 时箭头指向右，方向不得与流程语义相悖。\n- 每次只变化一个变量：一个节拍内只变更一个视觉变量，因果流程清晰可见。\n- 卡片内文字：文字框不得宽于卡片；若同宽必须 text_align=center，否则 x=card.x+(card.width-text.width)/2；禁止贴卡片左边缘。\n- 流程箭头：优先用文本 →；用 line 必须另加箭头头部，禁止无头 line；箭头起止在节点 bbox 外。\n- 多行文本：每行独立 text，同 x 不同 y；y[i+1]=y[i]+font_size*line_height+24；禁止共用 y/bbox。\n- 重复文本：同一句/同一关键词只能出现一个 text；需要两行就拆成不重叠两行。\n- 边框语义：text 的 border_* 是文本框边框，不是字形描边；默认不给 text 加 border；字形描边用 text_stroke，卡片用 shape+text，外发光用 box_shadow。\n'


MG_BATCH_CONTRACT = (
    "MG 批量生成统一用 generate_mg_batch；每项返回 mg_def/issues/preview_path。 "
    "生成前 validate_animation_clip，挂时间线前 preview_animation_clip；"
    "预览返回 grid_path 后可用 read_image(path=grid_path) 让视觉模型看网格；失败项不拖垮整批。"
)
