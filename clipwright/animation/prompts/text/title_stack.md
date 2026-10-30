# 标题堆叠（title_stack）

## Intent

用主标题 + 副标题（可选一行说明）建立信息层级，一段话讲清"这是什么、给谁看"。

## Semantic constraints

- 层数 ≤3；主标题 ≤12 字、副标题 ≤14 字、说明行 ≤14 字
- 主/副标题必须来自 brief 或旁白摘要，禁止自造口号
- 主标题字号 > 副标题字号 > 说明字号，层级靠字号与字距，不靠装饰
- `position=bottom` 禁止；整体底边 ≤920px，整体高度 ≤画面高 45%

## Composition

1920×1080 基准：主标题 48–64px 粗体、副标题 28–34px、说明 24–28px；
层间距 ≥12px；左对齐或居中皆可，必须落在安全区内（x=8%–92%）。

## Motion

阶段表（片段 2.0s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 主标题 | 0 | 0.4 | title | opacity 0→1、translate_y 12→0（ease-out） |
| 副标题 | 0.25 | 0.6 | subtitle | opacity 0→1、translate_y 12→0（ease-out） |
| 说明 | 0.5 | 0.85 | note | opacity 0→1（ease-out） |
| 收束 | 1.2 | 1.3 | 三层 | scale 1.0→1.015→1.0（ease-out，极轻微，收住画面） |
| 静止 | 1.3 | 2.0 | 三层 | 无变化 |

整体入场 ≤1.2s；drawtext 版可把三层合成一个主文本层整体入场，独立错峰分层仅 MG HTML。

## Anti-patterns

- 主标题写成长句（>12 字）
- 三层各自不同动效方向，画面乱
- 靠加底板/描边撑层级
- 主副标题字号差 <8px（看不出层级）

## Example

```json
{
  "animation_id": "title_stack",
  "width": 1920,
  "height": 1080,
  "duration_sec": 2.0,
  "elements": [
    {
      "id": "t",
      "type": "text",
      "content": "一期讲透剪辑",
      "position": "center",
      "x": 960,
      "y": 380,
      "props": {
        "font_size": 116,
        "color": "#111111",
        "font_weight": 700
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 12,
          "easing": "ease-out",
          "scale": 1.0
        },
        {
          "time": 0.4,
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
      "id": "s",
      "type": "text",
      "content": "从素材到成片",
      "position": "center",
      "x": 960,
      "y": 470,
      "props": {
        "font_size": 60,
        "color": "#B23A2F"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 12,
          "easing": "ease-out"
        },
        {
          "time": 0.25,
          "opacity": 0,
          "translate_y": 12,
          "easing": "ease-out"
        },
        {
          "time": 0.6,
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
    },
    {
      "id": "n",
      "type": "text",
      "content": "第 3 期",
      "position": "center",
      "x": 960,
      "y": 540,
      "props": {
        "font_size": 44,
        "color": "#111111"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "easing": "ease-out"
        },
        {
          "time": 0.5,
          "opacity": 0,
          "easing": "ease-out"
        },
        {
          "time": 0.85,
          "opacity": 1,
          "easing": "linear"
        },
        {
          "time": 1.8,
          "opacity": 1
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S10, S12, S1]

主画风 S10 水墨留白（`prompts/styles/S10.md`）：留白控节奏、朱砂一点；
备选 S12 电影字幕标题（字距定层次）、S1 星光线（暗底）。

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