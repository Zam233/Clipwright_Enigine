# 插件开发文档改版记录（zam_plugin_development）

本目录保存 **2026-11-21 插件开发文档改版** 的原始改版稿与变更记录；正式开发文档在
[`docs/plugin/`](../plugin/README.md)。

## 本次改版做了什么

- 删除旧的 5 份插件开发文档：
  - `docs/plugin_authoring.md`
  - `docs/plugin-sdk.md`
  - `docs/plugin-ui-layout-language.md`
  - `docs/PLUGIN_AGENT_INTEGRATION.md`
  - `docs/plugin_config_frontend_integration.md`
- 原重写稿（单文件版）保留在本目录：`plugin-development.md`；
- 按 AstrBot 插件开发文档的组织方式，把内容拆到 `docs/plugin/` 的多页文档；
- 更新 `AGENTS.md`、`docs/README.md`、`docs/api_reference.md` 的指向。

## 旧内容 → 新页面

| 旧文档 | 新页面 |
|---|---|
| `plugin_authoring.md`（contributions/扩展点） | `plugin/contributions.md`、`plugin/simple.md` |
| `plugin_authoring.md`（UI 槽位） | `plugin/ui.md` |
| `plugin-sdk.md`（子进程 SDK/RPC） | `plugin/subprocess.md` |
| `plugin-ui-layout-language.md` | `plugin/ui.md` |
| `PLUGIN_AGENT_INTEGRATION.md`（提示词） | `plugin/contributions.md`、`plugin/providers.md` |
| `plugin_config_frontend_integration.md` | `plugin/config.md` |
| 架构/生命周期/API/迁移 | `plugin/other.md` |

## 参考

- 组织方式参考 AstrBot 插件开发文档：<https://docs.astrbot.app/dev/star/plugin-new.html>
- 当前实现：`clipwright/plugins/`、`clipwright/services/plugin_*.py`、`clipwright/schema/plugin.py`、
  `src/features/plugins/`
