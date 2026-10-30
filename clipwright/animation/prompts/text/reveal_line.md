# 逐行揭示（reveal_line）

## Intent

让一句话按行依次出现，用时间顺序表达因果/递进；每一行都必须能独立读懂。

## Semantic constraints

- 每行 ≤14 字，最多两行（超过先摘要，不得硬塞）
- 行与行之间语义递进或并列，禁止把一句话随机切两半
- 文本只来自旁白/原文摘要；不新增原文没有的结论
- `position=bottom` 禁止；两行整体底边 ≤920px

## Composition

两行左对齐或居中，行间 12–16px；整块垂直位置 y=380–560px；
背景实拍可见，不加不透明底板（需要底板时走 S9 柔光玻璃，且标注「MG HTML 路径」）。

## Motion

阶段表（片段 2.0s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 第一行 | 0 | 0.5 | line1 | translate_y 16→0、opacity 0→1（ease-out） |
| 第二行 | 0.3 | 0.8 | line2 | translate_y 16→0、opacity 0→1（ease-out，错峰 0.3s） |
| 收束 | 1.2 | 1.3 | 两行 | scale 1.0→1.015→1.0（ease-out，极轻微，收住画面） |
| 静止 | 1.3 | 2.0 | 两行 | 无变化 |

入场总时长 ≤0.8s，之后静止 ≥0.6s；遮罩揭示属于 MG HTML 能力，drawtext 不做（本期不落）。

## Anti-patterns

- 两行同时出现（等于没有揭示感）
- 单行超过 14 字
- 用遮罩/裁剪当入场（drawtext 不支持，标注「MG HTML 路径」）
- 每行都加不同动效，节奏散乱

## Example

```json
{
  "animation_id": "reveal_line",
  "width": 1920,
  "height": 1080,
  "duration_sec": 2.0,
  "elements": [
    {
      "id": "l1",
      "type": "text",
      "content": "先把需求说清楚",
      "position": "center",
      "x": 960,
      "y": 400,
      "props": {
        "font_size": 72,
        "color": "#F4F6F8"
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
          "time": 1.1,
          "scale": 1.0,
          "easing": "ease-out",
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 1.2,
          "scale": 1.015,
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 1.3,
          "scale": 1.0,
          "easing": "linear",
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 1.8,
          "opacity": 1,
          "translate_y": 0,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "l2",
      "type": "text",
      "content": "再谈风格与时长",
      "position": "center",
      "x": 960,
      "y": 480,
      "props": {
        "font_size": 64,
        "color": "#E4572E"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 16,
          "easing": "ease-out"
        },
        {
          "time": 0.3,
          "opacity": 0,
          "translate_y": 16,
          "easing": "ease-out"
        },
        {
          "time": 0.8,
          "opacity": 1,
          "translate_y": 0,
          "easing": "linear"
        },
        {
          "time": 1.8,
          "opacity": 1,
          "translate_y": 0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S12, S1]

主画风 S12 电影字幕标题（`prompts/styles/S12.md`）：字号/字距定层次、位移 8–24px；
备选 S1 星光线（暗底单主体）。不叠加两个模块的运动约束。

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