# clipwright-motion-plugin

ClipWright（帧艺）的独立动效视频插件：Agent 读写工程文件、写 React/TSX 场景，
渲染时由插件用 **Remotion** 执行并返回标准媒体，engine 只做通用校验与合成。

- 插件 id：`motion_scene`
- ClipKind：`motion_scene_clip`（`media_overlay.v1`，渲染回调 `motion_render`）
- 工程目录（插件命名空间）：`plugins/motion_scene/<project_id>/motion/`
- 运行时：`runtime/`（插件自有；engine 只按 job broker 执行）

## 工具

| 工具 | 作用 |
|---|---|
| `motion_project_init` | 初始化 `motion/project.json` + `motion/scenes/` |
| `motion_scene_write` | 写 `motion/scenes/scene-XXX.tsx`（支持 `expected_revision`） |
| `motion_scene_read` | 读工程/场景源码（返回 sha256） |
| `motion_scene_validate` | 用运行时渲染第 0 帧，验证场景可执行 |
| `motion_preview` | 渲染指定帧 PNG（定调帧），登记 `preview` artifact |
| `motion_render` | 渲染时回调：Remotion 出 ProRes 4444 透明视频，登记 `render` artifact |

场景契约：默认导出 React 组件，可用 `useCurrentFrame` / `useVideoConfig`；
渲染时 `metadata.scene_path` 指定场景文件（默认 `motion/scenes/scene-001.tsx`）。

## 安装

1. 安装运行时依赖（只需一次）：

```bash
cd runtime
npm install
```

2. 把插件目录放到 ClipWright 插件目录，或按插件安装流程导入。
3. 登记 `node` runtime（管理员）：

```bash
curl -X POST http://127.0.0.1:8080/api/plugin/runtimes \
  -H "Authorization: Bearer $CLIPWRIGHT_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"runtime_id":"node","executable":"C:/Program Files/nodejs/node.exe","version":"24","approved":true}'
```

4. 加载插件：`POST /api/plugin/load/motion_scene`。

Chrome 默认自动探测（Windows Chrome/Edge、macOS、Linux 常见路径），也可用
`CHROME_PATH` 覆盖（需在宿主环境里可见）。

## 验证

```bash
# 在装有 clipwright 的 Python 中（PYTHONPATH 指向 engine 仓库）
python scripts/smoke.py .
python scripts/e2e_render.py .
```

`e2e_render.py` 会真实初始化工程、写 TSX、经 engine job broker 调 Remotion，
产出 30fps/1s 的 ProRes 4444 透明视频并登记 artifact。

## 边界

- 插件不修改 engine；engine 不解析 `motion/project.json`、`scene-*.tsx`。
- 插件只在 worker/job 内执行；engine 不执行时间线里的脚本。
- 运行时依赖（React/Remotion/Chrome）属于插件，不属于 engine。

## 已知限制

- `runtime/node_modules` 不入库，首次使用需 `npm install`。
- 未实现自动化测试套件（当前用 smoke/e2e 脚本验证）。
- 预览只做单帧 PNG；多帧预览片、字幕/音频混流尚未接入。
