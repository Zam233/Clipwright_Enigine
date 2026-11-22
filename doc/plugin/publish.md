# 发布与签名

发布前先保证：manifest 合法、贡献声明与运行时一致、权限最小、通过体检脚本。Clipwright 当前
以本地插件目录/管理页安装为主，没有独立的公开市场流程；下面是被加载器实际校验的部分。

## 1. 发布前检查清单

- [ ] `id` 与目录名一致，`api_version: "2"`；
- [ ] 每个贡献都在 manifest 的 `contributions` 中声明；
- [ ] `GET /api/plugin/list` 的 `contributions` 与声明一致，`declared_only` 为空；
- [ ] 权限只声明真正需要的（`network` / `http` / `fs_write`），并在允许列表内；
- [ ] `python scripts/plugin_health_report.py` 无“零贡献/旧轨/未声明注册”问题；
- [ ] `python scripts/plugin_contribution_snapshot.py out.json` 可复现贡献面；
- [ ] `ruff check` 通过；插件代码不 import 核心内部私有模块来绕权限；
- [ ] 需要覆盖内置时：`override_builtin: true` + 签名 + 管理员策略，且说明理由。

## 2. manifest 签名

签名覆盖除 `signature` 外的全部 manifest 字段（HMAC-SHA256，按字段名排序保证确定性）。

```bash
# 生成密钥（保存到 CLIPWRIGHT_PLUGIN_SIGNATURE_KEY）
python scripts/plugin_sign.py genkey > plugin_signing_key.txt

# 只打印签名，不写文件
python scripts/plugin_sign.py sign plugins/my_plugin --key=<hex|utf8>

# 写回 plugin.yaml
python scripts/plugin_sign.py sign plugins/my_plugin --key=<key> --write

# 校验
python scripts/plugin_sign.py verify plugins/my_plugin --key=<key>
```

策略：

- `plugin_require_signature=true` 时，未签名插件拒绝加载；
- 配置了显式 `plugin_signature_key` 时，同样要求签名（堵“部署了密钥但默认不强制”的缺口）；
- 签名不匹配 → 拒绝加载，错误写入插件错误通道。

## 3. 权限

manifest 的 `permissions` 必须 ⊆ `settings.plugin_allowed_permissions`。子进程插件还有：

- `runtime_config.capabilities`：额外能力授权（默认只有 `media_probe/frame_check/align`）；
- `runtime_config.exposes`：允许别的插件调用哪些 `tool:` / `skill:`；
- `runtime_config.extra_read_roots`：额外只读目录（谨慎使用）。

`fs_write` 只允许写插件自己的 `PluginData` 命名空间，不是任意路径。

## 4. 前端 bundle

如果插件带声明式 UI bundle：

- 把 `bundle.html` 的 SHA-256 写进 manifest 的 `bundle_sha256`；
- 该字段进入签名载荷，运行时读取时会重算摘要；
- bundle 不允许任意脚本/外链，安全边界按 `docs/genui.md` 与前端 CSP 执行；
- 不需要 bundle 时留空，不要为了“看起来完整”硬塞。

## 5. 依赖与版本

- `dependencies`：依赖的插件 id；未安装、被禁用、加载失败都算缺失，加载会被拒绝；
- 插件 `version` 由你自己维护；`config_schema_version` 变更时，插件目录可提供
  `migrations.py` 做配置迁移；
- 不要依赖插件目录里的相对路径读写数据——升级/重装会丢，数据放 `PluginData`。

## 6. 安装/卸载

- 安装：把插件目录放到 `plugins/` 或 `PluginData/plugins/`，或走管理页安装流程；
- 加载/重载：`POST /api/plugin/load/{id}`；
- 停用：`POST /api/plugin/{id}/disable`（配置持久化）；
- 卸载：`DELETE /api/plugin/{id}`；卸载会调 `shutdown()` 并回收该 owner 的贡献；
- 卸载前确认没有别的插件 `dependencies` 依赖它。

## 7. 发布后回归

1. 冷启动加载（不是热重载）也成功；
2. 停用后贡献被回收，`GET /api/plugin/contributions` 不再出现；
3. 重载失败不会把旧实例留在半可用状态；
4. 错误通道可读、日志有插件 id 前缀；
5. 对子进程插件：kill worker 后宿主能标记 dead 并给出可读错误，不会静默挂起。
