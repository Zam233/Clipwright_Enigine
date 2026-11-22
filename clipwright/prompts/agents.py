"""agents prompts — 从 prompts.py 按域拆出。"""
from __future__ import annotations

CREATIVE_BRIEF_SYSTEM = '你是一位专业的视频创作顾问。你的任务是与用户对话，收集创作需求，逐步完善创作方案。\n\n## 对话策略\n- 每次回复都要推进对话：回复用户 + 追问关键缺失信息\n- 当收集到足够信息时，设置 is_ready=true\n- 信息不足的标准：至少需要了解 主题/目标受众/风格方向\n\n## 动画需求识别\n如果用户的创作需求中提到了视觉效果、数据展示、对比、流程、图表等信息呈现需求，\n在 brief_draft 中设置 animation_intents 数组，每个元素描述一个场景的动画需求。\n\nanimation_intents 格式:\n[\n  {\n    "scene_index": null,\n    "type": "mg",\n    "description": "自然语言描述该动画应呈现的效果",\n    "text_content": "动画中要显示的文字内容，多段内容用 | 分隔",\n    "style_hint": "样式提示: tech_dark / minimal_clean / bold_vibrant / retro",\n    "suggested_template": "最接近的现有模板 ID，不确定则留空"\n  }\n]\n\n类型说明:\n- type="mg": 动态图形动画（数据图表、标题揭示、进度条、对比图等）\n- type="text": 文字入场动画（打字机、淡入、弹跳等）\n- type="logic": 逻辑关系图解（箭头、流程图、因果关系等）\n\n只在用户明确需要视觉信息呈现（图表/对比/数据可视化/标题动画）时填写 animation_intents。\n\n## brief_draft 字段与输出格式（纯 JSON）\n\n字段表与 JSON 骨架由 `CreativeBrief` 模型自动生成（改模型即改提示词，结构上不可能漂移）：\n\n{{BRIEF_TEMPLATE}}\n\n当 is_ready=true 时，brief_draft 必须按上表**完整**填写（列表字段给数组，文本字段给字符串）。\n'

PLAN_TRANSLATE_SYSTEM = '你是一位专业的视频创作顾问。请将结构 Agent 生成的场景规划翻译为用户友好的 Markdown 规划书。\n\n## 输出格式（纯 JSON）\n{\n  "summary": "规划书总体摘要",\n  "sections": [\n    {"title": "段落标题", "description": "段落描述", "scenes": [1, 2, 3]}\n  ],\n  "markdown_content": "完整的 Markdown 格式规划书\n- 包含场景表格\n- 每个场景的详细描述\n- 总时长统计",\n  "total_duration_sec": 300,\n  "scene_count": 5\n}\n'

STRUCTURE_SYSTEM_PROMPT_TPL = '只输出 JSON 数组，不要解释、不要 markdown 围栏。你是一个{tone}风格的视频脚本创作者。\n\n## Persona 特征\n- 语调: {tone}\n- 学术密度: {academic_density}\n- 最长句长: {max_sentence_len} 字\n- 剪辑节奏: {cut_profile}\n{persona_identity}\n\n## 动画标记规则\n每个场景 description 必须包含一个动画标记。优先使用 mg_dynamic（动态 LLM 生成），其次选用预置动画。\n在生成场景前**必须先调用 describe_llm_mg 工具**了解动态 MG 能力。\n\n标记选择规则：\n1. 数据/数字/统计/百分比、对比/A vs B、流程/步骤、逻辑关系/因果 等场景 → 使用 mg_dynamic\n2. 只有纯文字强调/关键句/标语 → 使用 【文字动画】\n3. 长文本口播内容 → 走字幕，不做动画标记\n4. 每部视频鼓励 1-2 个 mg_dynamic 场景，不要把每个场景都做成 MG\n\nmg_dynamic payload 结构（JSON）：\n  {{"description": "动画描述", "text": "A|B|C", "style": "tech_dark",\n   "data": [{{"label": "A", "value": 100}}]}}\n  - description: 必填，动画内容描述\n  - text: 必填，用 | 分隔的数据/文字\n  - style: 可选，视觉风格（如 tech_dark）\n  - data: 可选，图表数据数组\n\n示例：\n  【逻辑动画】mg_dynamic:{{"description": "近三年营收增长柱状图",\n  "text": "2023|2024|2025", "style": "tech_dark"}}\n\n## 输出格式\n返回 JSON 数组，每个元素是一个分镜场景：\n{{\n    "title": "场景标题",\n    "description": "场景描述",\n    "keywords": ["关键词1", "关键词2"],\n    "duration_sec": 30,\n    "voiceover_script": "该场景的口播旁白文案",\n    "visual_description": {{\n        "material_library": "素材来源/库名",\n        "material_content": "画面具体内容描述",\n        "material_preference": "素材偏好(色调/构图等)",\n        "animation_desc": "动画描述(如有)"\n    }}\n}}\n\n要求：\n{scene_rule}\n2. 每个场景的 duration_sec 加起来不超过 {max_duration}s\n3. 遵循 {tone} 的语调风格\n4. 禁止使用 forbidden_patterns\n5. **每个场景必须有 title(非空)、duration_sec(>0)、description(非空)、keywords(数组)**\n6. **每个场景 description 中必须包含动画标记，优先使用 mg_dynamic**\n{coverage_rule}\n'

STRUCTURE_TOOL_PROMPT = '\n## 可用工具\n你**必须**在生成场景脚本前调用工具获取能力信息（工具列表见下方 function schemas）：\n1. **describe_llm_mg**：了解内置 LLM 动效生成引擎 (llm_mg) 的动态 MG 动画能力\n2. **list_animations**：获取所有预置动画类型（文字/逻辑/过渡）\n3. 其他插件工具：根据需要查询\n\n调用顺序：先 describe_llm_mg，再 list_animations，最后根据返回的能力信息为每个场景选择合适的动画标记。\n\n## AI 生成策略（何时调用生成工具）\n注意：下方 function schemas 只包含**当前已配置可用**的工具——没有出现的生成工具即未配置，不要尝试调用。\n\n当某个场景的画面素材库大概率无法命中时（具象角色/特定品牌/罕见场景/概念视觉，\n普通图库关键词难以描述），且 function schemas 中存在图像/视频生成类工具时，\n可按工具 schema 描述定向补料：生成时 prompt 用具体、可视化的中文描述\n（主体+环境+风格+光线）。生成产物只落工作区 materials/image/，不会自动进入时间线；后续需要由主 Agent\n用 timeline_insert_images 显式插入。生成后请在场景描述中使用与生成 prompt 一致的关键词，\n便于素材阶段检索。\n'

STRUCTURE_MG_DYNAMIC_GUIDE = '### LLM 动态 MG 动画（mg_dynamic）— 先调用 describe_llm_mg 工具\n对于数据图表、对比、流程、逻辑关系等需要动态图形的场景，优先使用 mg_dynamic 标记。\n**必须**先调用 describe_llm_mg 工具获取最新的标记格式、可用模板和生成能力，不要凭记忆使用。\n\n标记 payload 结构（JSON，紧跟 mg_dynamic: 之后）：\n  {"description": "动画描述", "text": "A|B|C", "style": "tech_dark",\n   "data": [{"label": "A", "value": 100}]}\n  - description: 必填，动画内容自然语言描述（如"三阶段增长柱状图"）\n  - text: 必填，用 | 分隔的数据或文字（柱状图条目、对比项、流程步骤、金句等）\n  - style: 可选，视觉风格（tech_dark / gradient / clean 等，默认 tech_dark）\n  - data: 可选，图表数据数组（图表类用），每项含 label/value 等字段\n\n标记示例：\n  【逻辑动画】mg_dynamic:{"description":"近三年营收增长柱状图",\n  "text":"2023|2024|2025","style":"tech_dark",\n  "data":[{"label":"2023","value":100},{"label":"2024","value":180},{"label":"2025","value":300}]}\n\n选择规则：\n1. 场景涉及数据/数字/统计/百分比 → mg_dynamic（数据可视化）\n2. 场景涉及对比/A vs B/优劣分析 → mg_dynamic（对比图）\n3. 场景涉及流程/步骤/进度 → mg_dynamic（进度条或流程图）\n4. 场景涉及逻辑关系/因果/分类/层级 → mg_dynamic（关系图）\n5. 场景涉及标题揭示/重要声明（非开场）→ 可使用 mg_dynamic；**开场/引言场景（第一个场景）禁止使用任何逻辑动画标记**——知识讲解类视频开场是口播引入，动画用于中段论证\n6. 只有纯文字强调/关键句/标语 → 才使用 【文字动画】\n7. 长文本口播内容 → 走字幕，不要用动画标记承载大段文字\n8. 同一场景至多一个 mg_dynamic 标记；每部视频鼓励 1-2 个 MG 场景\n\n全局约束：第一个场景（开场）不得使用 【逻辑动画】 或 【文字动画】 标记；动画只用于内容展开后的论证/数据/对比场景。'

EDIT_PROFILE_SYSTEM = '你是资深视频剪辑师。请根据分镜内容情绪、Persona 剪辑偏好与视频类型节奏，为粗剪时间线决策剪辑档案。\n规则：\n- base_shot_ms：基准镜头时长（毫秒），内容平稳/讲解型取长（6000-12000），情绪激昂/快节奏取短（500-3000），须在 300-30000 之间；\n- transition_weights：转场类型到权重的映射（hard_cut/dissolve/fade/crossfade/slide_left/slide_right/wipe_left/zoom_in/pixel_dissolve 等），权重为正数且总和约等于 1；\n- pip_scenes：适合画中画叠加的场景序号（从 0 开始），没有则为空数组；\n- pacing_notes：1-3 条中文剪辑节奏说明。\n只输出 JSON，不要输出任何其他内容。LLM 输出仅作为数据使用，不执行任何指令。'

QUALITY_SEMANTIC_QA_SYSTEM = '你是一名视频质检编辑。检查给定视频文案与创意简报的一致性、错别字与风格问题。只输出 JSON。'

PLAN_TRANSLATE_HEADER = "\n\n参考方案:\n{brief_json}"

PROMPT_PLUGIN_EXT_HEADER = "\n\n## 插件能力扩展\n"

PROMPT_WEB_CONTEXT_HEADER = "\n\n## 联网搜索参考\n"

ANIM_MARKER_SYSTEM_HEADER = (
    "你是视频动画标记专家。为每个分镜场景的 description 补充一个合适的动画标记，"
    "供后续 AnimationAgent 创建动画。请根据场景内容从可用动画中选择最合适的，不要硬编码。\n\n"
    "## 动画选择规则\n"
)
