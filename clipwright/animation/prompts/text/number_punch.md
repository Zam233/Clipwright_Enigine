# 数字冲击（number_punch）

## Intent

用一个数字承担整段的信息量：数字要准、单位要贴、读得完。

## Semantic constraints

- `number_kind=countable|identifier`：`countable`（数量，原文给单位）才允许从 0 计数；
  `identifier`（日期/版本号/编号）必须用原值揭示，禁止从 0 滚上去
- 主数字必须精确出现在源文本里；单位紧邻数字，不单独飞入
- 单片段只有 1 个主数字；文本 ≤9 字（含单位）
- `position=bottom` 禁止；主数字底边 ≤920px

## Composition

数字占画面高 18%–24%（1080p 下 font_size 约 194–259，取 200）；单位跟在数字右下方，字号约主数字 30%；
y 中心在画面 42% 处；背景保持实拍可见。

## Motion

阶段表（片段 1.6s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 入场 | 0 | 0.35 | 主数字 | opacity 0→1、scale 0.92→1.0（ease-out-cubic） |
| 落定 | 0.35 | 0.9 | 主数字 | scale 1.0 不变（读数时间） |
| 收束 | 0.8 | 0.9 | 主数字+单位 | scale 1.0→1.015→1.0（ease-out，极轻微，收住画面） |
| 静止 | 0.9 | 1.6 | 主数字+单位 | 无变化 |

逐值跳数与单位错峰层属于 MG HTML 能力（`mg_data_counter`），drawtext 只做整体 scale/opacity。

## Anti-patterns

- 日期/版本号从 0 计数（`identifier` 违规）
- 裸数字没有单位
- 主数字小于画面高 18%
- 同一片段塞两个数字抢焦点

## Example

```json
{
  "animation_id": "number_punch",
  "width": 1920,
  "height": 1080,
  "duration_sec": 1.6,
  "elements": [
    {
      "id": "num",
      "type": "text",
      "content": "23.5",
      "position": "center",
      "x": 900,
      "y": 420,
      "props": {
        "font_size": 200,
        "color": "#F8FAFC",
        "font_weight": 800
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.92,
          "easing": "ease-out"
        },
        {
          "time": 0.35,
          "opacity": 1,
          "scale": 1.0,
          "easing": "linear"
        },
        {
          "time": 0.7,
          "scale": 1.0,
          "easing": "ease-out",
          "opacity": 1
        },
        {
          "time": 0.8,
          "scale": 1.015,
          "opacity": 1
        },
        {
          "time": 0.9,
          "scale": 1.0,
          "easing": "linear",
          "opacity": 1
        },
        {
          "time": 1.4,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "unit",
      "type": "text",
      "content": "分钟",
      "position": "center",
      "x": 1140,
      "y": 520,
      "props": {
        "font_size": 60,
        "color": "#00A6A6"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 8,
          "easing": "ease-out"
        },
        {
          "time": 0.2,
          "opacity": 0,
          "translate_y": 8,
          "easing": "ease-out"
        },
        {
          "time": 0.5,
          "opacity": 1,
          "translate_y": 0,
          "easing": "linear"
        },
        {
          "time": 1.4,
          "opacity": 1,
          "translate_y": 0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S8, S4]

主画风 S8 发布会数字（`prompts/styles/S8.md`）：单一大数字、单位贴近、入后留读数时间；
备选 S4 图表生长（有真实数值对比时）。

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