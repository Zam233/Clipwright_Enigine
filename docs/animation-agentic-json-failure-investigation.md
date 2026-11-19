# Agentic 动画 JSON 失败调查

## 结论

`generate_mg` 本身没有稳定失败。01 的主要失败是**决策 JSON 被 max_tokens=300 截断**，但错误被包装成 `MGGenerator: Expecting ',' delimiter`，看起来像 MG 生成失败。

## 证据

`renders/agentic-llm-1.txt`：决策 443 字符，正常 JSON。

`renders/agentic-llm-2.txt`：389 字符，在 `reason` 中间截断：

```json
"reason":"当前只有角色与意图，尚无 mg_def；先固化动画简报，明确标题、留白、下划线、风格与 hold 节奏，作为后续草
```

没有闭合 `}`；`json.loads` 在 char 321 报 `Expecting ',' delimiter`。

当时 `decide_with_llm` 使用：

```python
max_tokens=300
```

并允许模型输出很长的 `args` / `reason`，导致决策 JSON 超限。

## 为什么像 generate_mg 失败

`_generate_mg_via_agentic` 的 `except` 统一包装成：

```text
agentic loop 失败，回退 MGGenerator: <异常>
```

所以决策解析错误被显示成 MGGenerator 错误。

## 已修

- `decide_with_llm` max_tokens 300 → 1200；
- reason 限制 40 字；
- 决策 JSON 解析失败时回退 `generate_mg`；
- loop capped 后补一次 `generate_mg`，保证样片产出；
- `generate_mg` 工具级重试 2 次。

01 单测已恢复 `success=true, elements=2`。

## 其他 generate_mg 失败原因

从 `agentic-demo6.log` 可见 MG JSON 校验错误：

- keyframes 里出现 `x` / `y` / `content`（不允许动画属性）；
- `shape` 元素缺 `shape` 字段；
- easing `snap` 不在白名单；
- 字体 `sfmono-regular` 没有 @font-face，HTML lint 失败；
- 05 三行文字共用 y/bbox，semantic gate 拒绝。

这些都是 MG schema/validator 问题，不是 Agentic loop 问题。

## 后续

- 决策 prompt 不要输出大 args；只给 tool 名和最少参数；
- 决策 JSON schema 固定 `reason<=40`；
- MG 生成 prompt 明确 keyframes 只允许 opacity/scale/translate/rotate 等白名单；
- validator 对 `x/y/content` 做丢弃并记录，而不是整份失败；
- easing `snap` 映射为 `ease-out` 或报错重试；
- font family 只允许已注册字体。
