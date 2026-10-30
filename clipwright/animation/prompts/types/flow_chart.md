# 流程判断（flow_chart）

## Intent

用节点与有向边说明判断/分支/循环，让读者看清"从哪来、到哪去、什么条件"。

## Semantic constraints

- 节点含 `id`、`label`、`source_ref`；边含 `from`、`to`、`relation`、`source_ref`
- 节点 2–6 个；边的两端必须存在；分支条件只能来自原文（禁止自造条件）
- 关系类型限：顺序（→）、判断（?）、循环（↺）
- 无来源的节点/边不画；缺失则降级为文字说明

## Composition

自上而下或自左而右单一方向；节点同宽同高（宽 220–320px、高 80–120px）、间距 ≥40px；
连线 2px，箭头 10px；全部落在安全区，底边 ≤920px；节点文字 ≤8 字。

## Motion

阶段表（片段 3.4s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 根节点 | 0 | 0.4 | root | opacity 0→1、scale 0.9→1 |
| 逐层节点 | 0.4 | 2.0 | 每层节点 | opacity 0→1，按边方向逐层，错峰 0.15–0.3s |
| 连线 | 0.6 | 2.2 | 每条边 | scale 沿方向 0→1（跟随目标节点） |
| 静止 | 2.2 | 3.4 | 全部 | 无变化 |

先根节点、再按边方向逐层；末段静止 ≥0.6s；连线不许同时全出现（那样看不到方向）。

## Anti-patterns

- 把「判断/分支/循环逻辑结构」当节点数据
- 自造分支条件
- 节点重叠或连边交叉打结
- 一次性所有节点同现

## Example

```json
{
  "animation_id": "flow_chart",
  "width": 1920,
  "height": 1080,
  "duration_sec": 3.4,
  "elements": [
    {
      "id": "n1",
      "type": "shape",
      "shape": "rect",
      "content": "写脚本",
      "position": "center",
      "x": 960,
      "y": 200,
      "props": {
        "width": 300,
        "height": 100,
        "color": "#26C6DA",
        "border_radius": 12
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.9,
          "easing": "ease-out"
        },
        {
          "time": 0.4,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.2,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "e1",
      "type": "line",
      "content": "",
      "position": "center",
      "x": 960,
      "y": 500,
      "props": {
        "width": 2,
        "height": 2,
        "color": "#DCE3E8"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_y": 0
        },
        {
          "time": 0.6,
          "opacity": 0,
          "translate_y": 0
        },
        {
          "time": 1.2,
          "opacity": 1,
          "translate_y": 0
        },
        {
          "time": 3.2,
          "opacity": 1,
          "translate_y": 0
        }
      ]
    },
    {
      "id": "n2",
      "type": "shape",
      "shape": "rect",
      "content": "配音",
      "position": "center",
      "x": 700,
      "y": 640,
      "props": {
        "width": 300,
        "height": 100,
        "color": "#26C6DA",
        "border_radius": 12
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.9,
          "easing": "ease-out"
        },
        {
          "time": 0.9,
          "opacity": 0,
          "scale": 0.9,
          "easing": "ease-out"
        },
        {
          "time": 1.6,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.2,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    },
    {
      "id": "n3",
      "type": "shape",
      "shape": "rect",
      "content": "出片",
      "position": "center",
      "x": 1220,
      "y": 640,
      "props": {
        "width": 300,
        "height": 100,
        "color": "#26C6DA",
        "border_radius": 12
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "scale": 0.9,
          "easing": "ease-out"
        },
        {
          "time": 1.2,
          "opacity": 0,
          "scale": 0.9,
          "easing": "ease-out"
        },
        {
          "time": 1.9,
          "opacity": 1,
          "scale": 1.0
        },
        {
          "time": 3.2,
          "opacity": 1,
          "scale": 1.0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S6, S3]

主画风 S6 技术标注（`prompts/styles/S6.md`）：细线连接、短标签、工程示意；
备选 S3 极简几何（少元素、留白）。指示线如需箭头图形走 mg_fastpath / MG HTML。

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
