"""chrome-headless-shell 插件 — 为动画渲染提供官方无头浏览器。

现象（用户 2026-10-28）：渲染动画时反复弹出**有头 Chrome 窗口**。
hyperframes 在检测到独显时会走 hardware 抓帧（headless 拿不到 GPU → 换有头），
而且每个动画一次 render 就启动一次浏览器。

本插件的职责：
1. 向 `renderer_backend` 贡献点登记 `chrome_headless_shell`（含二进制路径）；
2. 插件被加载时**自动安装** chrome-headless-shell（`npx @puppeteer/browsers`）；
3. 装上之后，`clipwright.services.headless_browser` 会让动画渲染改走这个无头
   专用二进制；没装则回退"系统 Chrome 无头 + 软件 GPU（CPU）"。
"""

from __future__ import annotations

import asyncio
from typing import Any

from clipwright.config import logger
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginKind, PluginManifest
from clipwright.services import headless_browser


class HeadlessChromePlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="headless_chrome",
        name="Chrome Headless Shell",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="为动画/MG 渲染提供 chrome-headless-shell（无窗口、CPU 渲染）",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        # 路径在**消费时**解析（安装可能发生在插件加载之后）
        from plugins.headless_chrome import shell as _shell

        return [
            Contribution(
                ContributionKind.RENDERER_BACKEND,
                headless_browser.HEADLESS_SHELL_ID,
                {
                    "path": _shell.find_shell(),
                    "resolve": _shell.find_shell,
                    "install": _shell.install_shell,
                    "install_sync": _shell.install_sync,
                    "install_by_download": _shell.install_by_download,
                    "probe_version": _shell.probe_version,
                    "install_state": _shell.install_state,
                    "install_dir": str(_shell.BROWSERS_ROOT),
                },
                meta={"kind": "browser", "purpose": "headless-animation-render"},
            ),
            # 前端全局提示（用户 2026-10-29）：插件已装但二进制缺失时，前端要弹框
            # 引导安装。**声明式**贡献：只给 probe/install 端点与文案，前端通用
            # 渲染器（AppOverlayHost）不认识 chrome 也能画出来——这样任何插件都能
            # 贡献「可安装依赖」提示，不需要在 App.tsx 里写死任何业务。
            Contribution(
                ContributionKind.UI_SLOT,
                "headless_chrome_install_prompt",
                {},
                meta={
                    "slot": "app.overlay",
                    "title": "动画渲染依赖 chrome-headless-shell",
                    "order": 10,
                    "payload": {
                        "kind": "installable-dependency",
                        # 用户 2026-10-29：不要每个界面都弹 → 一次后端启动只提示一次。
                        # 语义交给宿主实现：bootIdField 取自 probe 响应，
                        # 「本次启动已提示过」记在 localStorage，刷新不重弹，
                        # 后端重启（boot_id 变）才重新提示一次。
                        "once": {"scope": "backend-boot", "bootIdField": "boot_id"},
                        "probe": {
                            "endpoint": "/api/render/browser",
                            "path": "headless_shell",
                            "installedField": "installed",
                            "installingField": "installing",
                            "versionField": "version",
                            "resultField": "last_result",
                        },
                        "install": {
                            "endpoint": "/api/render/browser/install",
                            "method": "POST",
                            "pollMs": 2000,
                        },
                        "copy": {
                            "title": "动画渲染还缺 chrome-headless-shell",
                            # 关键：没装**不阻断**——已经默认走 CPU 无头渲染
                            "why": (
                                "现在已经在用「系统 Chrome 无头 + CPU 软件渲染」出动画，"
                                "功能正常，只是慢一些（长片更明显）。装上官方"
                                " chrome-headless-shell 能明显提速，属于可选优化。"
                            ),
                            "benefit": (
                                "装上之后动画渲染改走专用无头二进制：不弹窗、启动更快、长时间渲染更稳。"
                                "下载约 120MB（解包约 220MB），装到项目目录 _cache/browsers/，不写系统盘。"
                            ),
                            "installLabel": "立即安装",
                            "retryLabel": "重试安装",
                            "laterLabel": "知道了（本次启动不再提示）",
                            "installingLabel": "安装中…",
                            "successTitle": "chrome-headless-shell 已安装",
                            "successBody": (
                                "之后的动画与 MG 渲染会走这个专用无头二进制（不再弹浏览器窗口）。"
                                "已经在跑的渲染不受影响，新渲染立即生效。"
                            ),
                        },
                    },
                },
            ),
        ]

    def setup(self) -> None:
        """副作用入口：插件加载即确保 shell 可用（缺失则后台安装，不阻塞启动）。

        ⚠ 必须是 `setup()` 而不是 `initialize()`：新契约插件的 `initialize()`
        在 `contributions()` 非空时会被门面**短路**（base._install_dual_track_bridge
        → run_contributions 成功后直接 return），写在 initialize 里的安装逻辑
        永远跑不到——本插件之前正是因此从未自动安装过。
        """
        if headless_browser.find_shell():
            logger.info("headless_chrome: 已就绪（%s）", headless_browser.find_shell())
            return
        logger.info("headless_chrome: 未找到 chrome-headless-shell，开始安装…")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            headless_browser.install_sync()
            return
        loop.create_task(headless_browser.install_shell())

    def shutdown(self) -> None:
        return None


__all__ = ["HeadlessChromePlugin"]
