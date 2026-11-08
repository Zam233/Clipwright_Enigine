# 引语卡（quote_block）

## Intent

把一段引文连同出处一起交代清楚：先让人读到话，再让人知道这话是谁说的。

## Semantic constraints

- 结构固定为 `quote_text`、`attribution` 两个字段；**没有出处就不生成来源**，宁可只放引文
- 引文逐字来自原文，禁止改写、拼接、补充语气词
- 每行 ≤14 字，最多两行；超长先摘要并在 `attribution` 里保留原始出处
- `position=bottom` 禁止；正文主层底边 ≤920px

## Composition

正文主层固定在上部 70% 区域内（y=300–620px），底边不超过 920px；
引号与正文合成一个主文本层整体入场；需要独立卡片底/引号图形/分隔线时走 MG HTML。

## Motion

阶段表（片段 2.4s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 浮入 | 0 | 0.5 | 引文层 | translate_y 16→0、opacity 0→1（ease-out） |
| 出处 | 0.4 | 0.8 | attribution | opacity 0→1（ease-out，错峰 0.4s） |
| 收束 | 1.5 | 1.6 | 两层 | scale 1.0→1.015→1.0（ease-out，极轻微，收住画面） |
| 静止 | 1.6 | 2.4 | 两层 | 无变化（读引文时间 ≥1.0s） |

drawtext 最终只输出 1 个可见文本层 + 1 个出处层；引号装饰如果用图形，标注「MG HTML 路径」。

## Anti-patterns

- 伪造引文或出处
- 引文与字幕逐字重复（字幕已经在讲，画面就别再抄一遍）
- 不透明大底板压满画面
- 两行以上长段引文（超过就摘要）

## Example

```json
{
  "animation_id": "quote_block",
  "width": 1920,
  "height": 1080,
  "duration_sec": 2.4,
  "elements": [
    {
      "id": "quote",
      "type": "text",
      "content": "慢就是快",
      "position": "center",
      "x": 960,
      "y": 380,
      "props": {
        "font_size": 88,
        "color": "#222222"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 16,
          "easing": "ease-out",
          "scale": 1.0
        },
        {
          "time": 0.5,
          "opacity": 1,
          "translate_y": 0,
          "easing": "linear",
          "scale": 1.0
        },
        {
          "time": 1.4,
          "scale": 1.0,
          "easing": "ease-out",
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 1.5,
          "scale": 1.015,
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 1.6,
          "scale": 1.0,
          "easing": "linear",
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 2.2,
          "opacity": 1,
          "translate_y": 0,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "by",
      "type": "text",
      "content": "— 某剪辑师",
      "position": "center",
      "x": 960,
      "y": 480,
      "props": {
        "font_size": 40,
        "color": "#C44536"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "easing": "ease-out"
        },
        {
          "time": 0.4,
          "opacity": 0,
          "easing": "ease-out"
        },
        {
          "time": 0.8,
          "opacity": 1,
          "easing": "linear"
        },
        {
          "time": 2.2,
          "opacity": 1
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S7, S10]

主画风 S7 编辑部引语（`prompts/styles/S7.md`）：引号-正文-出处三层阅读层级；
备选 S10 水墨留白（黑白 + 一点朱砂，慢入场）。

## Constants

| 常量 | 值 |
|---|---|
| 画布 | 1920 x 1080（任何分辨率按 `font_size * width / 1920` 缩放） |
| 字幕带 | 底部 160px；元素底边不得超过 y=920px |
| 单行字数上限 | ≤14 字 |
| 元素数上限 | ≤3（含装饰） |
| 片段时长 | min_duration=0.8，max_duration=8.0；关键帧 ≤ 实际 duration_sec |
| 安全区 | x=8%–92%，y=8%–85% |

## Output

只输出一个 JSON 对象（`animation_id` + `elements`，元素字段 `id/type/content/position/x/y/props/keyframes`）。
关键帧为 `{"time": 秒, <属性>: 值, ...}` 的升序数组，每个元素至少 2 帧；不加围栏、不加注释、不写"其余同上"。

## Self-check

- [ ] 关键帧 time 全部落在 0–duration_sec，且严格升序。
- [ ] 元素数 ≤3，单行 ≤14 字，底边 ≤920px，不压字幕带。
- [ ] 主体至少一个属性真在变（opacity/scale/translate 之一），不是只加装饰。
- [ ] 末段静止 ≥0.6s，动画在 1.2s 内完成。
- [ ] 每个事实值都能追到旁白/原文；同输入两次生成的关键帧时间完全一致。

## Fallback

1. 解析失败按裸 JSON → ```json 围栏 → 首个 `{` 至末个 `}` 依次重试。
2. 结构问题交给 `_normalize`：时长钳制、关键帧夹取到片段内、元素截断到 3 个、文字键收敛到 `content`。
3. 校验（`validate_text_def`）失败最多重试 2 次；仍失败返回失败并走模板/降级 kind。
4. 文本超长先语义摘要，禁止静默截断；与字幕带冲突时先移 upper/center，再减元素。
5. 降级必须记 type_id、prompt_version、失败字段与 fallback kind；不得把回退说成生成成功。