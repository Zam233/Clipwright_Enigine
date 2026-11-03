# 标注贴片（label_callout）

## Intent

在画面上给某个对象贴一个短标签，说明"这是什么"。标签服务于对象，不抢主体。

## Semantic constraints

- 每标签 ≤8 字；每片段最多 2 个标签
- 必须有 `target_ref`（对象标识）与 `target_position`（归一化坐标 0–1）；**没有坐标就降级为静态标签**，不编造位置
- 标签文本来自原文/旁白；不新增原文没有的结论
- 避让人脸与字幕带：`position=bottom` 禁止，标签底边 ≤920px

## Composition

标签与对象的距离用画面比例表达（0.01–0.02 倍画宽），不用像素硬编码；
标签贴对象右上方，留 12px 内边距；需要一条指示线连到对象时走 mg_fastpath / MG HTML。

## Motion

阶段表（片段 1.6s）：

| 阶段 | t_start | t_end | 目标 | 属性起止 |
|---|---|---|---|---|
| 入场 | 0 | 0.3 | 标签 | translate_x 8→0、opacity 0→1（ease-out） |
| 收束 | 0.8 | 0.9 | 标签 | scale 1.0→1.015→1.0（ease-out，极轻微，收住画面） |
| 静止 | 0.9 | 1.6 | 标签 | 无变化 |

描边扫光、光晕扫过属于 MG HTML 能力，drawtext 不做（本期不落）。

## Anti-patterns

- 悬空标签（没有 `target_ref` 也没坐标还在"指"某处）
- 标签压住人脸或字幕带
- 一条 8 字标签配一整段说明
- 用 drawtext 假装能画指示线（标注「MG HTML 路径」）

## Example

```json
{
  "animation_id": "label_callout",
  "width": 1920,
  "height": 1080,
  "duration_sec": 1.6,
  "elements": [
    {
      "id": "lb",
      "type": "text",
      "content": "降噪麦",
      "position": "center",
      "x": 1180,
      "y": 360,
      "props": {
        "font_size": 44,
        "color": "#DCE3E8"
      },
      "keyframes": [
        {
          "time": 0,
          "opacity": 0,
          "translate_x": 8,
          "easing": "ease-out",
          "scale": 1.0
        },
        {
          "time": 0.3,
          "opacity": 1,
          "translate_x": 0,
          "easing": "linear",
          "scale": 1.0
        },
        {
          "time": 0.7,
          "scale": 1.0,
          "easing": "ease-out",
          "opacity": 1,
          "translate_x": 0
        },
        {
          "time": 0.8,
          "scale": 1.015,
          "opacity": 1,
          "translate_x": 0
        },
        {
          "time": 0.9,
          "scale": 1.0,
          "easing": "linear",
          "opacity": 1,
          "translate_x": 0
        },
        {
          "time": 1.4,
          "opacity": 1,
          "translate_x": 0,
          "scale": 1.0
        }
      ]
    }
  ]
}
```

## Style reference

style_modules: [S6, S3]

主画风 S6 技术标注（`prompts/styles/S6.md`）：细线牵引、短标签、工程感；
备选 S3 极简几何。指示线需要图形层时走 MG HTML。

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