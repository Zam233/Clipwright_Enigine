# 安全部署指南

帧艺 ClipWright 后端默认以**开发模式**运行（API 开放访问）。生产或公网部署前必须完成以下配置。

## 1. API 令牌认证

设置 `CLIPWRIGHT_API_TOKEN` 后，所有 `/api/*` 请求（`/api/health` 除外）必须携带：

```
Authorization: Bearer <token>
```

生成强随机令牌：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

未设置令牌时，服务启动会打印警告：`API 处于开放开发模式`。

前端请求携带令牌示例（Axios）：

```ts
apiClient.defaults.headers.common['Authorization'] = `Bearer ${token}`;
```

## 2. CORS 来源收紧

令牌模式下 CORS 仅允许 `CLIPWRIGHT_CORS_ORIGINS` 列出的来源（逗号分隔），默认：

```
CLIPWRIGHT_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

开发模式（无令牌）下 CORS 为 `*`。

## 3. 文件访问白名单

以下文件类端点仅允许访问白名单目录（`renders/`、`library/`、`editor_projects/`、`projects/`、`PluginData/`、Persona 目录、TTS 输出目录）内的文件，越界请求返回 400：

- `GET /api/render/video`、`GET /api/render/thumbnail`
- 代理生成（ProxyGenerator）的输入/输出路径
- Persona / 编辑器项目的 ID 强制 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`，阻止路径遍历

## 4. 上传限制

`POST /api/asset/upload` 分块读取并限制单文件 ≤ 2GB，超限返回 413。

## 5. 已知限制

- MongoDB 使用同步驱动（pymongo）；高并发场景建议迁移至 motor 或对查询调用 `asyncio.to_thread` 封装（`/metrics` 已封装）。
- 工具/插件执行端点（`/api/tool/execute`、`/api/plugin/load/*`）权限等同管理员操作，**必须**在令牌模式下部署。
