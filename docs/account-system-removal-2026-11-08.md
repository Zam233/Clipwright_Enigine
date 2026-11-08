# 账号系统剥离记录（2026-11-08）

> 分支：`feat/zam_remove_account_system`（engine 与前端同名分支）
> 目标：把 ClipWright Server（`:8090`，账号 + 市场）相关依赖从 engine 与前端彻底剥离，
> 保留本地单用户模式与本地 API 令牌。

## 1. 为什么剥离

- 前端 32 条路由里只有 2 页依赖账号服务（6.3%），但「曾登录 + Server 停机」会把
  `/projects` `/editor` `/export` `/persona*` 共 5 条守卫路由全部踢到不可用的 `/login`，
  等于把唯一的剪辑入口锁死；
- engine 362 条路由里只有 6 条 `/api/market/*` 是 Server 代理（1.7%），
  启动与鉴权链路 0 依赖（JWT 用共享密钥本地验签，不回查 Server）；
- 账号系统的实际承载能力（计费扣减）前端从未接线，收益低于维护成本。

## 2. engine 移除清单

| 移除项 | 说明 |
| --- | --- |
| `clipwright/api/market.py` | 6 条 `/api/market/*` 路由（列表/详情/安装） |
| `clipwright/services/market_client.py` | 对 Server 的 httpx 客户端 |
| `install_service.install_plugin/install_persona` | 从市场下载后安装；保留 `install_*_from_bytes()` 本地包安装 |
| `account_verify_mode` | `off/token/jwt` 三模式删除，仅保留 `api_token` 本地令牌 |
| `account_url`、`account_jwt_algorithm` | 不再需要 Server 地址与 JWT 算法 |
| `_verify_access_jwt()` / JWT 中间件分支 / SSE token 的 jwt 分支 | 不再接受 Server 签发令牌 |
| `account_jwt_secret` | 改名 `local_secret`（兼容旧环境变量），仅用于插件签名与 Webhook 加密派生 |

保留并保持不变：

- `CLIPWRIGHT_API_TOKEN`：唯一的鉴权手段；设置后 `/api/*`、`/metrics`、`/test` 需 `Authorization: Bearer`；
- `owner_id` 字段与 `authz.py` 辅助函数：保留为兼容层，`user_id` 恒为 `None`，不做隔离；
- 插件签名、Webhook 加密：改用 `CLIPWRIGHT_LOCAL_SECRET`（旧 `CLIPWRIGHT_ACCOUNT_JWT_SECRET` 仍可读）。

## 3. 前端移除清单

| 移除项 | 说明 |
| --- | --- |
| `services/api/account.ts` / `market.ts` | 登录、注册、刷新、退出、CREADIT、市场浏览/安装/评分/发布 |
| `stores/authStore.ts` / `services/api/session.ts` | 账号会话；本地令牌统一由 `settingsStore.authToken` 提供 |
| `pages/LoginPage.tsx` / `MarketPage.tsx` 及 `/login` `/market` 路由 | 页面与入口一并删除 |
| `requireSession` / `requireAdmin` 守卫 | 账号体系消失后无意义 |
| `cw:unauthorized` 续期监听、`cw_had_session` 标记 | 401 直接提示检查本地令牌 |
| 首页用户卡片 / CREADIT / 充值 / 登录注册入口 / 市场入口 | UI 剥离 |

## 4. 迁移指引

1. 若原本通过 Server 登录使用，改用本地令牌：engine `.env` 设 `CLIPWRIGHT_API_TOKEN=...`，
   前端「设置 → 后端连接」填同一个令牌；
2. 未配置令牌时保持开放（仅回环地址），与剥离前的 `off` 模式一致；
3. 已用旧 `CLIPWRIGHT_ACCOUNT_JWT_SECRET` 派生插件签名/Webhook 密钥的部署，
   继续保留该环境变量即可（作为 `CLIPWRIGHT_LOCAL_SECRET` 的兼容别名）。

## 5. 验证

- engine：`tests/clipwright/test_versions_api.py`、`test_recycle_bin.py`、`test_webhook_crypto.py`、
  `test_install_service.py` 通过（jwt/owner 隔离用例随能力移除而删除）；
- 前端：`npx tsc --noEmit` 通过；`npm run test` 140 文件 / 1223 通过；
- e2e：`e2e/no-account-system.spec.ts` 断言无 `/srv` 请求、无市场/登录入口、`/login` `/market` 落 404、
  `cw_had_session=1` 也能直接进项目页与编辑器；其余 e2e 套件回归通过。
