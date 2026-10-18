# Spike 结论（2026-10-18）

> 开发计划 §2 要求：每个 Spike 的结论必须写成"结论 / 数字 / 影响"三行，否则等于没做。
> 本文件是门 1（渲染可行）的证据底稿。

环境：Windows 11、Python 3.14.2、Node v24.11.0、`hyperframes@0.8.171`（`npm i -D hyperframes`，125 包，16s）、ffmpeg 可用、GPU 为 RTX 4070 Ti（HyperFrames 探测到 hardware WebGL）。

---

## SP1：场景脚本能渲，且两次渲染逐帧一致 ✅

**结论**：`window.__scene.renderFrame(t, ctx)` + `window.__timelines[compId].seek(t)` 这条契约在本机成立；同一份合成两次渲染 **24/24 帧 sha256 全等**。

**数字**（`python scripts/spike_scene_render.py --seconds 1 --fps 24 --width 320 --height 180`）：

| 项 | 值 |
|---|---|
| 帧数 | 24（1s @ 24fps） |
| 逐帧哈希 | 两次渲染全等（`first_diff: null`，`diffs: []`） |
| 单次渲染耗时 | 5.73s / 5.56s（含浏览器启动与编译） |
| MOV 体积 | 534KB（rgba，`needsAlpha: true`） |

**影响**：门 1 的"确定性"要件**已拿到证据**；R-01 可以从"生成 HTML 正确"升级为"真渲出帧且可复现"。

---

## SP1-附：一个必须记下来的坑（全局冻结会打死平台）

**结论**：**不能**在页面里全局替换 `setTimeout` / `setInterval` / `requestAnimationFrame` —— HyperFrames 自己的播放器与逐帧捕获依赖它们。

**证据**（首次尝试的输出）：

```
[Browser:PAGEERROR] [deterministic] setTimeout 不可用于场景渲染，请改为由 t 推导
✗  Render failed
   HyperFrames runtime failed: Error: [deterministic] requestAnimationFrame 不可用于场景渲染…
```

**影响**：`animation/scene_renderer.py` 改为**作用域内遮蔽**——把作者脚本包进
`(function (Math, Date, setTimeout, setInterval, requestAnimationFrame) { … })(…safe…)`，
作者拿到安全替身，平台继续用真全局。单测锁定该行为（`test_does_not_patch_globals`）。

---

## SP1-附二：`ais_available()` 冷启动必返回 False（会误导判断）

**结论**：`HyperframesRenderer.ais_available()` 走的是"非阻塞缓存读"，**新进程首次调用**
一定返回 `False`（后台线程才去探测）。判断"能不能渲"必须用 `await_available(timeout=…)`。

**数字**：`_probe_hyperframes()` 直接调用返回 `True`；同一进程 `asyncio.run(ais_available())` 返回 `False`。

**影响**：所有验收脚本/CI 一律用 `await_available()`；这也是 `scripts/spike_scene_render.py` 的第一处注释。

---

## SP2：1080p60 成本实测 ✅

命令：`python scripts/spike_scene_render.py --seconds 5 --fps 60 --width 1920 --height 1080`

**结论**：1080p60 全帧合成在本机可用，且**同样逐帧可复现**（300/300 帧 sha256 全等）。

| 项 | 值 |
|---|---|
| 帧数 | 300（5s @ 60fps，1920×1080，不透明背景） |
| 逐帧哈希 | 两次渲染全等（`first_diff: null`） |
| 单次渲染耗时 | 22.49s / 22.23s（含浏览器启动，单 worker） |
| MOV 体积 | 19.0MB（rgba）→ 约 3.8MB/秒 |

**换算**：约 **4.5 秒渲染 / 1 秒成片**（简单场景，单 worker）。一支 3 分 32 秒的 MV（212s）
≈ **16 分钟**渲染，落在 D15 默认预算（1080p60 单支 ≤30 分钟）之内；复杂版画/字符画场景会更高，
需按 M1 的实际效果再测一轮。

**影响**：
- 门 1 的"成本可接受"要件成立；
- 磁盘需预留（19MB/5s ≈ 230MB/分钟，中间产物另计）；
- 4K 必须按 D12 只渲指定帧，否则体积与耗时都不可接受。
