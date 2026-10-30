# 折线趋势（line_chart）

## Intent

用有序数据点表达趋势：读者要看到"往哪走"，不是看一堆数字。

## Semantic constraints

- 至少 3 个点，每点含 `x_label`、`y_value`、`unit`、`source_ref`；x 必须与输入顺序一致
- 只有 2 个点时只做对比、**不输出趋势结论**
- 缺单位或缺来源的点直接剔除；剔除后不足 3 点则改走并列卡片
- 不描绘原文没有的拐点、极值、预测

## Composition

绘图区 x=12%–88%、y=20%–70%；x 轴刻度等距、y 轴从 0 起；折线 2–3px；
数据点半径 6px；轴标签 24px；底边 ≤920px；面积填充为 MG HTML 可选项。

## Motion

阶段表（片段 3.2s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 轴 | 0 | 0.3 | 轴 | opacity 0→1 |
| 描线 | 0.3 | 1.8 | 折线 | scale 沿 x 0→1（按点顺序，每点 0.12–0.2s） |
| 数据点 | 0.5 | 2.0 | 每点 | opacity 0→1、scale 0.6→1（跟随折线位置） |
| 静止 | 2.0 | 3.2 | 全部 | 无变化 |

末段静止 ≥0.6s；描线完成后不再抖动；渐变面积/发光点属于 MG HTML 能力，本期不落。

## Anti-patterns

- 把「趋势变化，带面积填充 + 数据点」当数据输入
- 两点连线就断言"持续上升"
- x 顺序与输入不一致（造成假趋势）
- 非零 y 轴起点

## Example

```json
{
  "animation_id": "line_chart",
  "width": 1920,
  "height": 1080,
  "duration_sec": 3.2,
  "elements": [
    {
      "id": "axis",
      "type": "line",
      "content": "",
      "position": "center",
      "x": 960,
      "y": 760,
      "props": {
        "width": 1400,
        "height": 2,
        "color": "#3A86FF"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0
        },
        {
          "time": 0.3,
          "opacity": 1
        },
        {
          "time": 3.0,
          "opacity": 1
        }
      ]
    },
    {
      "id": "p1",
      "type": "circle",
      "content": "1 月 12",
      "position": "center",
      "x": 520,
      "y": 640,
      "props": {
        "width": 12,
        "height": 12,
        "color": "#FFBE0B"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 0.5,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 0.8,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.0,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "p2",
      "type": "circle",
      "content": "2 月 18",
      "position": "center",
      "x": 960,
      "y": 560,
      "props": {
        "width": 12,
        "height": 12,
        "color": "#FFBE0B"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 1.1,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 1.4,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.0,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "p3",
      "type": "circle",
      "content": "3 月 23",
      "position": "center",
      "x": 1400,
      "y": 500,
      "props": {
        "width": 12,
        "height": 12,
        "color": "#FFBE0B"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 1.7,
          "opacity": 0,
          "scale": 0.6,
          "easing": "ease-out"
        },
        {
          "time": 2.0,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.0,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S4, S1]

主画风 S4 图表生长（`prompts/styles/S4.md`）或 S1 星光线（暗底 + 光轨暗示方向）；
备选 S6 技术标注（需要读数标注时）。面积填充/发光需 MG HTML，本期不落 drawtext。

## Constants

| 常量 | 值 |
|---|---|
| 画布 | 1920 x 1080（默认；其他分辨率按比例换算） |
| 字幕带 | 底部 160px；所有图元底边 ≤920px |
| 节点/柱/点上限 | ≤6（可读性优先，超过就拆片段） |
| 片段时长 | min_duration=1.5，max_duration=12.0 |
| 安全区 | x=8%–92%，y=8%–85% |

## Output

只输出一个 JSON 对象：`animation_id` + `elements`（每个元素 `id/type/content/position/x/y/props/keyframes`）；
关键帧为升序 `{"time": 秒, <属性>: 值, ...}`，每元素至少 2 帧。禁止注释、围栏、"其余同上"。

## Self-check

- [ ] 每个数值/标签都能追到 `source_ref`；没有来源的不画。
- [ ] 元素数、坐标都在安全区内，底边 ≤920px，不压字幕带。
- [ ] 关键帧 time 在 0–duration_sec 内且升序；末段静止 ≥0.6s。
- [ ] 同输入两次生成的结构与时间点一致（无随机）。
- [ ] 只用了 MG validator 支持的属性与 easing（linear / ease / ease-in / ease-out / ease-in-out / back-out / elastic-out / bounce）。
## Fallback

1. 解析失败按裸 JSON → ```json 围栏 → 首个 `{` 至末个 `}` 依次重试。
2. `_normalize` 负责结构收敛（时长钳制、关键帧夹取、元素截断到 6）；归一化不等于语义修复。
3. `validate_mg_json` 失败最多重试 2 次；仍失败返回失败并降级到模板 kind。
4. 数据点不足（<3 点 / 缺单位 / 缺来源）：不画数值图，改强调文字或并列卡片，并在结果里说明原因。
5. 降级必须记 type_id、prompt_version、失败字段、fallback kind；不得把回退描述成生成成功。
