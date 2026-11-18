# 柱状对比（bar_chart）

## Intent

用柱高比较**同一量纲**的几个值，让读者一眼看出高低差，而不是读数字。

## Semantic constraints

- 每根柱必须同时有 `label`、`value`、`unit`、`source_ref`；缺任一 → 不画该柱
- 柱子数量 2–6；同一张图只能同一单位（元/人/次…），混单位就拆图
- 只允许零基线；排序只在该类型语义允许时使用（对比可不排序，排名才排序）
- 数值来自原文/数据表，不估算、不四舍五入到"好看"的数

## Composition

绘图区占画面 x=12%–88%、y=20%–70%；同宽柱、间距 = 柱宽 0.4 倍；轴标签 24–28px；
数值标签压在柱顶上方 8px；底边不超过 920px，不压字幕带；配色用所选风格模块 2 色 + 1 强调色。

## Motion

阶段表（片段 3.0s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 轴 | 0 | 0.3 | 轴元素 | opacity 0→1 |
| 柱体生长 | 0.3 | 1.5 | 每根柱 | scale 0→1（纵向，错峰 0.12–0.22s） |
| 数值标签 | 1.2 | 1.9 | 标签 | opacity 0→1（跟随各自柱） |
| 静止 | 1.9 | 3.0 | 全部 | 无变化 |

按输入顺序生长，不改数据顺序；末段静止 ≥0.6s；面积/渐变填充属于 MG HTML 能力，可选项不写进关键帧。

## Anti-patterns

- 把「数据对比，垂直柱状图」这种**类型描述**当数据输入
- 无来源的柱、无单位的裸数字
- 非零基线（视觉夸张）
- 混单位同图比较
- 柱数 >6 还硬画

## Example

```json
{
  "animation_id": "bar_chart",
  "width": 1920,
  "height": 1080,
  "duration_sec": 3.0,
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
          "time": 2.8,
          "opacity": 1
        }
      ]
    },
    {
      "id": "b1",
      "type": "shape",
      "shape": "rect",
      "content": "A 方案\\n1200 次",
      "position": "center",
      "x": 640,
      "y": 600,
      "props": {
        "width": 160,
        "height": 320,
        "color": "#3A86FF",
        "border_radius": 6
      },
      "keyframes": [
        {
          "time": 0,
          "scale": 0.0,
          "easing": "ease-out"
        },
        {
          "time": 0.3,
          "scale": 0.0,
          "easing": "ease-out"
        },
        {
          "time": 1.4,
          "scale": 1.0,
          "easing": "linear"
        },
        {
          "time": 2.8,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "b2",
      "type": "shape",
      "shape": "rect",
      "content": "B 方案\\n900 次",
      "position": "center",
      "x": 960,
      "y": 640,
      "props": {
        "width": 160,
        "height": 240,
        "color": "#FFBE0B",
        "border_radius": 6
      },
      "keyframes": [
        {
          "time": 0,
          "scale": 0.0,
          "easing": "ease-out"
        },
        {
          "time": 0.5,
          "scale": 0.0,
          "easing": "ease-out"
        },
        {
          "time": 1.5,
          "scale": 1.0,
          "easing": "linear"
        },
        {
          "time": 2.8,
          "scale": 1.0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S4, S3]

主画风 S4 图表生长（`prompts/styles/S4.md`）：深底、蓝黄数据色、按阅读顺序生长；
备选 S3 极简几何（单色 + 一个强调）。渐变/阴影按需走 MG HTML，mg_fastpath 不支持时降级纯色。

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
