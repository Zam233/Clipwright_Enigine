# {{type_name}}（{{type_id}}）MG 类型提示词 v2 骨架

> 这是**模板**，不是类型提示词本体：`text_specs.load_text_prompt` 只读同目录下的类型文件。
> 类型文件必须保留七段旧标题（Intent / Semantic constraints / Composition / Motion /
> Anti-patterns / Example / Style reference，`validate_text_registry` 会检查），
> 并按本骨架补上 Constants / Output / Self-check / Fallback 四段。

## ① 角色与交付物
你是 {{role}}，把输入 {{source_text}} 转换成一个 {{single_deliverable}}。
目标渲染路径：{{drawtext 关键帧 | mg_fastpath | MG HTML}}。只实现该路径明确支持的能力，
不得把静态描述冒充成已实现的动画。

## ② 硬约束
- 关键帧时间以片段局部秒计，满足 0 <= time <= {{duration_sec}}；按时间升序。
- 相同输入 + 相同提示词版本必须产生相同结构与关键帧；禁止逐帧依赖上一帧状态、禁止未固定随机。
- 只输出 JSON 对象，不加围栏、说明、注释；顶层必需 `animation_id` 与 `elements`。
- 文本行宽 <= {{max_line_chars}} 字（通用上限 14）；元素数 <= {{max_elements}}（通用上限 3）。
- 字幕带 = 画面底部 {{caption_band_px}}px（160）；所有元素必须避让字幕带与安全边界。
- 事实只能来自 {{source_fields}}；不得推算、补造、把推测写成结论。

## ③ 常量表
| 常量 | 值 |
|---|---|
| 画布 | {{width}} x {{height}}（默认 1920 x 1080） |
| 字幕带高度 | 160px |
| 单行文字上限 | {{max_line_chars}} 字 |
| 元素上限 | {{max_elements}} |
| 片段时长 | {{min_duration}}–{{max_duration}}s，本次 = {{duration_sec}}s |
| 安全区 | x=8%–92%，y=8%–{{safe_bottom_percent}}%（={{safe_bottom_px}}px） |

## ④ 分段关键帧公式与 easing 词典
先给不重叠的阶段表：{{阶段名 / t_start / t_end / 目标元素 / 属性起止值}}。
缺省属性保持静止；不得用形容词代替数值。

MG 路径允许的 easing（必须命中 `mg.validator.VALID_EASINGS` 或 cubic-bezier 数组）：
linear / ease / ease-in / ease-out / ease-in-out /
back-out / elastic-out / bounce / cubic-bezier(x1,y1,x2,y2)

禁止在 drawtext 提示词里使用上述 MG 名（drawtext 只认 `EASING_EXPR` 的 21 个名字）；
MG 提示词里出现 `粒子` / 模糊 / 3D / 遮罩 等属性时，必须在本行标注「MG HTML 路径」或「本期不落」。

## ⑤ 风格模块引用
主画风：{{S 编号}}（正文见 `prompts/styles/{{S 编号}}.md`）；只用该模块的配色、形状、线宽与层数。
运动方式：{{单一运动策略}}；不得叠加其它模块的运动约束。
版式：{{本类型版式}}；语义结构优先于风格参考。
禁止项：{{模块禁止项}}。

## ⑥ 不要清单
- 不要 {{禁止的事实或表达}}。
- 不要 {{该渲染路径不支持的属性}}。
- 不要引入参考风格里的文案/数据/品牌/人物。
- 不要靠加元素绕过 {{元素上限/版面约束}}。

## ⑦ 自检清单
- [ ] JSON 可解析、必需字段完整、无占位符残留。
- [ ] 关键帧时间在 0–{{duration_sec}}s 内且升序。
- [ ] 文本长度、元素数、字幕带、安全区全部合规。
- [ ] 主体有可观察属性变化（装饰变化不算）。
- [ ] 末段静止 >= {{read_hold_sec}}s。
- [ ] 每个事实值能追到输入来源；同输入再生成时间点不变。
- [ ] 所用属性都在 {{renderer}} 支持范围内。

## ⑧ 输出格式
只输出一个 JSON 对象，结构见：{{type_specific_json_schema}}。
禁止注释、围栏、"其余同上"。

## ⑨ 失败回退
1. 解析失败按 `_extract_json` 顺序重试：裸 JSON → ```json 围栏 → 首个 `{` 至末个 `}`。
2. 结构问题交给 `_normalize`：时长钳制、关键帧夹取、元素截断到 {{max_elements}}、文字键收敛到 `content`；归一化不算语义修复。
3. 校验失败最多重试 {{max_attempts}} 次；仍失败则返回失败并走模板/降级 kind。
4. 超长文本先语义摘要，禁止静默截断；字幕带冲突时移到 upper/center，仍冲突则减元素或降级。
5. 降级必须记 type_id、prompt_version、失败字段、fallback kind；不得把回退描述成成功生成。
