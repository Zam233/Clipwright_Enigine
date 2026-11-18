"""mg prompts — 从 prompts.py 按域拆出。"""
from __future__ import annotations

MG_STRICT_JSON_OUTPUT = '\n\n## 输出硬性要求\n- 只输出一个合法的 JSON 对象，不要包含任何解释性文字、前言或后语。\n- 不要使用 markdown 代码块围栏（``` 或 ```json），直接输出原始 JSON。\n- 输出中除 JSON 外不要出现任何其他字符。\n'

MG_NO_BACKGROUND_CONSTRAINT = '背景规则（PRD §17 D7）：动画叠加在实拍素材上，禁止**不透明全幅**底色与整屏遮挡；但允许并鼓励用半透明底板把文字与背景隔开——bg 元素的 background 只接受 rgba(0,0,0,0.25–0.55) 这类半透明值，高度不超过画面 45%。文字必须自带 text_shadow 或 text_stroke，或位于该底板内。'

MG_ORCHESTRATION_CONSTRAINTS = '\n\n## 动画编排约束\n- 顺序揭示（sequential reveal）：元素按阅读顺序逐个出现，重要数据点停顿 1-2 秒，避免多个元素同时动画。\n- 每节拍一个主运动（one primary motion per beat）：同一时刻只突出一个主运动，次级元素错峰（stagger）延迟入场。\n- 方向强化语义（direction reinforces semantics）：动画方向强化信息语义，增长向上、下降向下；流程类动画自左向右推进。\n- 构图居中（centered composition）：整体构图居中平衡，避免元素偏向一侧造成大面积留白。\n- 连线止于节点边缘（connectors stop at node edge）：连接线/箭头终止于节点边缘，不得穿入节点内部。\n- 箭头方向随流程（arrow direction follows flow）：流程 L→R 时箭头指向右，方向不得与流程语义相悖。\n- 每次只变化一个变量：一个节拍内只变更一个视觉变量，因果流程清晰可见。\n'
