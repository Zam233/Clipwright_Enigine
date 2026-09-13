"""HyperframesRenderer — 将所有 text/caption/logic overlay 渲染为透明 MOV。

核心流程：
1. 从 AnimationCatalog 获取所有 CSS @keyframes 定义
2. 为每个 overlay 生成 HTML div（含位置/动画/时序 data 属性）
3. JavaScript 精确管理入场动画 + 保持 + 出场淡出
4. Hyperframes render → 带 alpha 的 MOV
5. FFmpeg overlay 合成到主视频

回退：Hyperframes 不可用时，RenderService 降级到 drawtext。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clipwright.animation.catalog import AnimationCatalog
from clipwright.animation.hf_timeline import timeline_script
from clipwright.config import logger
from clipwright.services.async_util import cached_probe


def _probe_hyperframes() -> bool:
    """同步阻塞探测（在后台线程里跑，绝不进事件循环线程）。"""
    try:
        # 冷启动 npx 首次解析全局包可能超过 15s（下载/校验），放宽到 90s
        r = subprocess.run(
            [*HyperframesRenderer._cli_cmd("--version")],
            capture_output=True, text=False, timeout=90,
        )
        return r.returncode == 0
    except Exception:
        return False


# 进程级缓存探针：npx 冷启动慢，缓存 10 分钟，后台线程刷新，await 永不阻塞事件循环。
_hf_available = cached_probe("hyperframes", _probe_hyperframes, ttl=600.0, default=False)

# await_available() 的轮询间隔：npx 冷启动可能 ~90s，2s 轮询足够及时且不喧宾夺主。
_AWAIT_POLL_INTERVAL = 2.0


class HyperframesRenderer:
    """使用 Hyperframes (HTML→MOV) 渲染文字覆盖层。"""

    _BROWSER_CACHE_GLOB = (
        r"C:\Users\*\.cache\hyperframes\chrome\chrome-headless-shell\*\chrome-headless-shell-win64\chrome-headless-shell.exe"
    )

    @staticmethod
    def _local_bin() -> str | None:
        """本地固定安装的 hyperframes 可执行文件（优先于 npx，免冷启动）。

        解析顺序：
        1. 环境变量 ``HYPERFRAMES_BIN``（部署/CI 显式指定）
        2. 仓库根 ``node_modules/.bin/hyperframes[.cmd|.ps1]``（`npm i -D hyperframes`）
        3. 无 → 返回 None，调用方回退 ``npx hyperframes``
        """
        env_bin = os.environ.get("HYPERFRAMES_BIN")
        if env_bin and Path(env_bin).exists():
            return env_bin
        try:
            repo_root = Path(__file__).resolve().parents[2]
        except IndexError:  # pragma: no cover - 极浅目录布局
            return None
        for name in ("hyperframes.cmd", "hyperframes.ps1", "hyperframes"):
            candidate = repo_root / "node_modules" / ".bin" / name
            if candidate.exists():
                return str(candidate)
        return None

    @staticmethod
    def _cli_cmd(*args: str) -> list[str]:
        """构造 hyperframes CLI 命令（本地 bin 优先，回退 npx）。"""
        local = HyperframesRenderer._local_bin()
        if local:
            return [local, *args]
        return [HyperframesRenderer._npx_cmd(), "hyperframes", *args]

    @staticmethod
    def _quality_flag() -> str | None:
        """渲染质量档：draft / standard / high（环境变量可覆盖，缺省不传→上游 standard）。"""
        q = (os.environ.get("CLIPWRIGHT_HF_QUALITY") or "").strip().lower()
        return q if q in ("draft", "standard", "high") else None

    @staticmethod
    def _npx_cmd() -> str:
        import shutil
        candidates = [
            "npx", "npx.cmd",
            r"C:\Program Files\nodejs\npx",
            r"C:\Program Files\nodejs\npx.cmd",
            "/usr/local/bin/npx", "/usr/bin/npx",
        ]
        for c in candidates:
            if c in ("npx", "npx.cmd"):
                found = shutil.which(c)
                if found:
                    return found
            elif Path(c).exists():
                return c
        return "npx"

    @staticmethod
    def _resolve_browser_path() -> str:
        """定位 hyperframes 可用的 Chrome/Chromium（含已下载的 chrome-headless-shell）。

        hyperframes 在 Windows 上只会探测 ``C:\\Program Files\\Google\\Chrome\\...``，
        本机 Chrome 常装在 ``(x86)`` 目录 → 探测失败 → 尝试下载 → 渲染 hang。
        这里显式解析 headless shell 路径注入 ``HYPERFRAMES_BROWSER_PATH``。
        """
        import glob as _glob
        # 1) 用户已设置
        env_p = os.environ.get("HYPERFRAMES_BROWSER_PATH") or os.environ.get("PRODUCER_HEADLESS_SHELL_PATH")
        if env_p and Path(env_p).exists():
            return env_p
        # 2) hyperframes 自带的 chrome-headless-shell 缓存（优先，专为无头渲染设计）
        for pat in (
            r"C:\Users\*\.cache\hyperframes\chrome\chrome-headless-shell\*\chrome-headless-shell-win64\chrome-headless-shell.exe",
            r"C:\Users\*\AppData\Local\hyperframes\chrome\chrome-headless-shell\*\chrome-headless-shell-win64\chrome-headless-shell.exe",
        ):
            hits = _glob.glob(pat)
            if hits:
                return hits[0]
        # 3) 系统 Chrome / Edge
        for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ):
            if Path(p).exists():
                return p
        return ""

    @staticmethod
    def _render_env() -> dict | None:
        """为 hyperframes subprocess 提供带浏览器路径的 env（无浏览器时返回 None）。"""
        bp = HyperframesRenderer._resolve_browser_path()
        if not bp:
            return None
        env = dict(os.environ)
        env["HYPERFRAMES_BROWSER_PATH"] = bp
        env["PUPPETEER_EXECUTABLE_PATH"] = bp
        return env

    @staticmethod
    def is_available() -> bool:
        """同步、非阻塞读取缓存值（供 /health、启动期等 sync 上下文使用）。

        缓存为空时返回 False 并在后台触发一次探测，**不会**同步执行 npx。
        async 上下文请改用 ``await HyperframesRenderer.ais_available()``。
        """
        return bool(_hf_available.get_sync())

    @staticmethod
    async def ais_available() -> bool:
        """async 版可用检查：命中缓存立即返回，失效时后台刷新，永不阻塞事件循环。"""
        return bool(await _hf_available())

    @staticmethod
    async def lint_html(
        html: str, *, timeout: float = 120.0, run_cmd=None,
    ) -> tuple[bool, list[str]]:
        """上游静态门禁：对合成 HTML 跑 `hyperframes lint --json`（best-effort）。

        返回 ``(ok, findings)``：``ok=False`` 表示存在 **error 级** 问题（warning 不影响
        渲染，例如 `timeline_track_too_dense` / `studio_missing_editable_id`）。
        渲染器/CLI 不可用、超时、JSON 解析失败一律返回 ``(True, [])``——
        门禁失败绝不允许反向阻断渲染。

        run_cmd 与 ``render_mg_html_to_mov`` 同约定（注入可复用的追踪/并发治理）。
        """
        import json as _json
        import tempfile as _tempfile

        if not html:
            return True, []
        work = Path(_tempfile.mkdtemp(prefix="hf_lint_"))
        try:
            (work / "index.html").write_text(html, encoding="utf-8")
            cmd = HyperframesRenderer._cli_cmd("lint", str(work), "--json")
            try:
                if run_cmd is not None:
                    r = await run_cmd(cmd)
                    raw = getattr(r, "stdout", b"") or b""
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    except asyncio.TimeoutError:
                        proc.kill()
                        return True, []
            except Exception:
                return True, []
            if isinstance(raw, str):
                text = raw
            else:
                text = (raw or b"").decode("utf-8", errors="replace")
            payload = _json.loads(text or "{}")
            findings = payload.get("findings") or []
            messages = [
                f"[{f.get('severity')}] {f.get('code')}: {f.get('message')}"
                for f in findings
                if isinstance(f, dict)
            ]
            has_error = any(
                isinstance(f, dict) and f.get("severity") == "error" for f in findings
            )
            return (not has_error), messages
        except Exception:
            return True, []
        finally:
            import shutil as _shutil
            _shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    async def check_html(
        html: str, *, timeout: float = 300.0, run_cmd=None,
    ) -> tuple[bool, list[str]]:
        """上游浏览器门禁：`hyperframes check --json`（运行时 / 布局 / 动效 / WCAG 对比度）。

        比 ``lint_html`` 更强的地方：真的在 headless Chrome 里加载合成，能抓到运行时报错、
        文本溢出与越界、动效连续性，以及**文字对比度不达标**（本项目 MG 模板实测命中过
        1.6:1 < 3:1）。

        代价是每次要起一次 Chrome，因此只在生成期按需开启（`generation.check_gate`）。
        返回 ``(ok, findings)``；ok=False 表示存在 error 级问题；CLI 不可用/超时/解析失败
        一律返回 ``(True, [])``（best-effort，不得反向阻断渲染）。
        """
        import json as _json
        import tempfile as _tempfile

        if not html:
            return True, []
        work = Path(_tempfile.mkdtemp(prefix="hf_check_"))
        try:
            (work / "index.html").write_text(html, encoding="utf-8")
            cmd = HyperframesRenderer._cli_cmd("check", str(work), "--json")
            try:
                if run_cmd is not None:
                    r = await run_cmd(cmd)
                    raw = getattr(r, "stdout", b"") or b""
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    except asyncio.TimeoutError:
                        proc.kill()
                        return True, []
            except Exception:
                return True, []
            text = raw if isinstance(raw, str) else (raw or b"").decode("utf-8", errors="replace")
            payload = _json.loads(text or "{}")
            messages: list[str] = []
            has_error = False
            for stage in ("lint", "runtime", "layout", "motion", "contrast"):
                section = payload.get(stage)
                if not isinstance(section, dict):
                    continue
                for f in section.get("findings") or []:
                    if not isinstance(f, dict):
                        continue
                    severity = f.get("severity")
                    messages.append(
                        f"[{stage}/{severity}] {f.get('code')}: {f.get('message')}"
                    )
                    if severity == "error":
                        has_error = True
            return (not has_error), messages
        except Exception:
            return True, []
        finally:
            import shutil as _shutil
            _shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    async def render_mg_html_to_mov(
        html: str,
        out_dir: str,
        mov_path: str,
        fps: float = 30.0,
        timeout: float = 1800.0,
        run_cmd=None,
        validate=None,
        quality: str | None = None,
        lint_gate: bool = False,
        variables: dict | None = None,
    ) -> str | None:
        """P1-01: 单个 MG HTML → 独立目录 index.html → npx hyperframes render → MOV。

        预览端点与主管线渲染路径共用此实现（旧预览把 mg_html 塞进 overlay 列表，
        _overlay_to_html 只读 text 字段 → 恒产出空白视频）。

        run_cmd: 执行命令的 async 可调用（签名 (cmd, **kwargs) -> CompletedProcess 风格），
        由调用方注入以复用各自的追踪/并发治理；缺省用 asyncio.subprocess 直跑。
        quality: draft / standard / high（缺省读 CLIPWRIGHT_HF_QUALITY，预览建议 draft）。
        lint_gate: True 时渲染前跑 `hyperframes lint`，存在 error 级问题即放弃本次渲染
        （返回 None，由调用方走既有降级链）。
        variables: 传给上游 `--variables` 的变量覆盖（P1-5）。HTML 里已声明
        `data-composition-variables` 的 default，因此**不传也能正确渲染**；传了则以本次
        参数为准（同一份 HTML 可用于多组文案/配色，也是 `--batch` 的前置条件）。
        `CLIPWRIGHT_HF_STRICT_VARIABLES=1` 时追加 `--strict-variables`（CI 用：未声明键/
        类型不符直接失败，而不是静默降级）。
        返回 MOV 路径，失败返回 None。
        """
        import uuid as _uuid
        from pathlib import Path as _Path

        if not html:
            return None
        mg_dir = _Path(out_dir) / f"mg_{_uuid.uuid4().hex[:8]}"
        mg_dir.mkdir(parents=True, exist_ok=True)
        (mg_dir / "index.html").write_text(html, encoding="utf-8")

        if lint_gate:
            ok, findings = await HyperframesRenderer.lint_html(html, run_cmd=run_cmd)
            if not ok:
                logger.warning(
                    "HyperframesRenderer: MG HTML 未通过 lint 门禁: %s", " | ".join(findings[:5])
                )
                return None

        cmd = HyperframesRenderer._cli_cmd(
            "render", str(mg_dir), "-o", mov_path, "--format", "mov",
            "-f", str(int(fps)), "--quiet",
        )
        eff_quality = quality or HyperframesRenderer._quality_flag()
        if eff_quality:
            cmd += ["-q", eff_quality]
        if variables:
            import json as _json

            payload = _json.dumps(
                {str(k): "" if v is None else str(v) for k, v in variables.items() if k},
                ensure_ascii=False,
            )
            cmd += ["--variables", payload]
            if (os.environ.get("CLIPWRIGHT_HF_STRICT_VARIABLES") or "").strip() in ("1", "true", "yes"):
                cmd += ["--strict-variables"]
        try:
            if run_cmd is not None:
                r = await run_cmd(cmd)
                returncode = getattr(r, "returncode", 1)
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=HyperframesRenderer._render_env(),
                )
                returncode = await proc.wait()
        except Exception:
            return None
        if returncode != 0:
            return None
        # 有效性校验可由调用方注入（主管线用 _is_valid_video；缺省查文件存在）
        ok = validate(mov_path) if validate is not None else _Path(mov_path).exists()
        return mov_path if ok else None

    async def await_available(timeout: float = 120.0) -> bool:
        """轮询等待 Hyperframes 可用（冷启动预热用），超时返回 False。

        每 ``_AWAIT_POLL_INTERVAL``（默认 2s）调用一次缓存的探针（``_hf_available``），
        后台线程负责跑真实 npx 探测，事件循环只 await，绝不阻塞。一旦探针翻转为
        True 立即返回 True；超过 ``timeout`` 秒仍未就绪返回 False。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if bool(await _hf_available()):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(_AWAIT_POLL_INTERVAL, remaining))

    @staticmethod
    async def render_overlays(
        overlays: list[dict[str, Any]],
        output_path: str,
        width: int = 1920, height: int = 1080, fps: float = 30.0,
    ) -> str | None:
        """将所有 overlay 渲染为透明 MOV。"""
        if not overlays:
            return None
        if not await HyperframesRenderer.ais_available():
            logger.warning("HyperframesRenderer: Hyperframes 不可用")
            return None

        html = HyperframesRenderer._build_html(overlays, width, height, fps)
        work_dir = Path(tempfile.mkdtemp(prefix="hf_"))
        try:
            (work_dir / "index.html").write_text(html, encoding="utf-8")
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

            cmd = HyperframesRenderer._cli_cmd(
                "render", str(work_dir), "-o", str(out),
                "--format", "mov", "-f", str(int(fps)), "--quiet",
            )
            _q = HyperframesRenderer._quality_flag()
            if _q:
                cmd += ["-q", _q]
            logger.info("HyperframesRenderer: 渲染 %d 个覆盖层 → %s", len(overlays), output_path)
            # 同步 subprocess（最长 1h）offload 到线程池，避免冻住事件循环。
            # 注入 HYPERFRAMES_BROWSER_PATH，避免 Windows 上浏览器探测失败导致 hang。
            # 审计 P0 修复：走 run_tracked_ff，渲染取消时可 terminate（to_thread 自动传播 context）。
            from clipwright.services.render import run_tracked_ff
            result = await asyncio.to_thread(
                run_tracked_ff, cmd, capture_output=True, text=False, timeout=3600,
                env=HyperframesRenderer._render_env(),
            )
            if result.returncode == 0 and out.exists():
                logger.info("HyperframesRenderer: 完成 (%s, %.0fKB)",
                            output_path, out.stat().st_size / 1024)
                return str(out)
            else:
                err = result.stderr.decode("utf-8", errors="replace")[:300] if result.stderr else "unknown"
                logger.warning("HyperframesRenderer: 渲染失败(code=%d): %s", result.returncode, err)
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("HyperframesRenderer: 异常: %s", e)
            return None
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

    # ── HTML 生成 ─────────────────────────────────────────

    @staticmethod
    def _build_html(
        overlays: list[dict], width: int, height: int, fps: float,
    ) -> str:
        """生成完整 HTML，所有 overlay 统一渲染。"""
        total_dur = 0.0
        for ov in overlays:
            end = ov.get("start_sec", 0) + ov.get("duration_sec", 3)
            if end > total_dur:
                total_dur = end
        total_dur = max(total_dur, 1.0)

        css_kfs = AnimationCatalog.get_css_keyframes_all()
        elems: list[str] = []
        for i, ov in enumerate(overlays):
            elem = HyperframesRenderer._overlay_to_html(ov, i, width, height)
            if elem:
                elems.append(elem)

        # 注册真实时间轴（RuntimeTimelineLike）替代旧的 setTimeout 墙钟 JS：
        # seek-safe + 消除 producer 的 timeline 就绪轮询超时（实测 108.2s → 18.0s）。
        js = timeline_script()

        return f"""<!DOCTYPE html>
<html data-fps="{int(fps)}" data-width="{width}" data-height="{height}">
<head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{width}px;height:{height}px;overflow:hidden;background:transparent;position:relative}}
.hf-el{{position:absolute}}
.hf-diagram{{display:flex;align-items:center;gap:20px;font-family:sans-serif;color:#fff}}
.hf-diagram .item{{font-size:28px;padding:10px 20px;background:rgba(255,255,255,0.12);border-radius:8px;white-space:nowrap}}
.hf-diagram .arrow{{font-size:28px;color:#4f8cff;white-space:nowrap}}
{css_kfs}
</style></head><body>
<div id="root" data-composition-id="main" data-duration="{total_dur:.2f}"
     data-width="{width}" data-height="{height}"
     style="width:{width}px;height:{height}px;position:relative;overflow:hidden">
{chr(10).join(elems)}
</div>
<script>{js}</script>
</body></html>"""

    @staticmethod
    def _overlay_to_html(ov: dict, index: int, width: int, height: int) -> str:
        """单个 overlay → HTML div / SVG。"""
        text = (ov.get("text") or "")[:300]
        if not text:
            return ""
        start = ov.get("start_sec", 0)
        dur = ov.get("duration_sec", 3)
        renderer = ov.get("renderer", "")
        diagram_params = ov.get("diagram_params")
        anim_class = ov.get("anim_class", "hf-fade-in")
        anim_duration = ov.get("anim_duration", 0.5)
        font_size = ov.get("font_size", 48)
        font_color = ov.get("font_color", "#ffffff")
        position = ov.get("position", "bottom")

        # 逻辑图解 → SVG（使用 DiagramRenderer，支持逐元素入场 + 多种图解类型）
        if diagram_params:
            from clipwright.animation.diagram_svg import DiagramRenderer, DiagramStyle
            diagram_style_params = ov.get("diagram_style", {})
            ds = DiagramStyle.from_persona(diagram_style_params)
            ds.font_size = font_size
            ds.text_color = font_color
            svg = DiagramRenderer.render(diagram_params, ds, width, height)
            if svg:
                # 图解 SVG 自带逐元素入场动画（位于子树内），容器只声明时间窗口；
                # data-anim-subtree="1" 让运行时时间轴把子树里的 CSS 动画一并 seek。
                return (
                    f'<div class="clip hf-el hf-diagram-svg" id="hf-d{index}" data-i="d{index}" '
                    f'data-start="{start}" data-duration="{dur}" data-track-index="1" '
                    f'data-anim-class="hf-diagram-reveal" data-anim-dur="0.01" data-anim-subtree="1" '
                    f'style="position:absolute;top:0;left:0;width:{width}px;height:{height}px">'
                    f'{svg}</div>'
                )

        # 位置 CSS
        pos_css = _position_css(position)

        # 字幕用黑色半透明背景
        is_caption = ov.get("category") == "caption"
        bg_css = "background:rgba(0,0,0,0.55);padding:8px 20px;border-radius:6px" if is_caption else ""

        # 入场 + 出场两段 CSS 动画全部由运行时时间轴 seek（见 hf_timeline）：
        # - 入场：animation-fill-mode both（起始前保持 0% 帧），保持段由 forwards hold
        # - 出场：只有 forwards（不能 both！否则出场动画在 delay 期间会以「后声明者优先」
        #   覆盖入场的透明度，导致元素一出现就是全不透明）
        try:
            dur_f = max(0.0, float(dur))
        except (TypeError, ValueError):
            dur_f = 3.0
        exit_dur = min(0.3, dur_f * 0.15)
        exit_start = max(0.0, dur_f - exit_dur)
        try:
            anim_dur_f = max(0.01, float(anim_duration))
        except (TypeError, ValueError):
            anim_dur_f = 0.5
        anim_css = (
            f"animation:{anim_class} {anim_dur_f:.3f}s ease-out both,"
            f"hf-fade-out {exit_dur:.3f}s ease-in forwards;"
            f"animation-delay:0s,{exit_start:.3f}s;"
        )

        return (
            f'<div class="clip hf-el" id="hf-{index}" data-i="{index}" data-start="{start}" '
            f'data-duration="{dur}" data-track-index="1" '
            f'data-anim-class="{anim_class}" data-anim-dur="{anim_duration}" '
            f'style="{anim_css}font-size:{font_size}px;color:{font_color};'
            f'{pos_css};{bg_css}">{_html_esc(text)}</div>'
        )

    @staticmethod
    def _timing_js() -> str:
        """已废弃：保留为返回运行时时间轴片段（旧调用方/测试兼容）。

        审计 HFR-2：旧实现用 ``setTimeout`` 驱动 visibility + CSS animation，
        在逐帧 seek / 并行抓帧下不是 seek-safe，且没有注册 ``window.__timelines``
        导致每次渲染多付 45s+ 就绪轮询。现统一走 ``hf_timeline`` 的
        RuntimeTimelineLike 实现。
        """
        return timeline_script()

    @staticmethod
    def render_overlay_on_video(
        overlay_video: str, main_video: str, output_path: str,
        start_sec: float = 0, duration_sec: float = 0,
    ) -> bool:
        """将 HF 输出的 MOV 叠加到主视频。

        start_sec/duration_sec 指定叠加窗口（默认 0 = 全时长叠加，保持旧行为）。
        提供窗口时用 setpts 归零覆盖层时间戳，并用 overlay enable=between(t,start,start+dur)
        只在窗口内显示；若覆盖层时长超过 duration_sec，则用 -t 截断输入对齐窗口。
        """
        try:
            # Bug3 合并：编码器走 render 的智能探测（GPU 可用时 h264_nvenc，否则 libx264）
            from clipwright.services.render import (
                _resolve_encoder, _hwaccel_args, _current_pix_fmt, run_tracked_ff,
            )
            encoder = _resolve_encoder()
            hwaccel = _hwaccel_args(encoder)
            pix_fmt = _current_pix_fmt()

            if start_sec > 0 or duration_sec > 0:
                # 数值以原始形式格式化：int 保持 "265"、float 保留 "0.0"/"12.5"
                # （两套测试契约的窗口格式均由此满足）
                fc = (
                    f"[1:v]setpts=PTS-STARTPTS+{start_sec}/TB,format=yuva420p[ov];"
                    f"[0:v][ov]overlay=x=0:y=0:enable='between(t,{start_sec},{start_sec + duration_sec})'"
                    ":eof_action=pass[vout]"
                )
            else:
                fc = "[0:v][1:v]overlay=format=auto[vout]"

            inputs = ["-i", main_video]
            if duration_sec > 0 and _mov_longer_than(overlay_video, duration_sec):
                # 覆盖层超长 → 用 -t 截断输入，使 enable 窗口与内容对齐
                inputs += ["-t", _fmt_sec(duration_sec)]
            inputs += ["-i", overlay_video]

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                *hwaccel,
                *inputs,
                "-filter_complex", fc,
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", encoder, "-pix_fmt", pix_fmt,
                "-c:a", "copy", output_path,
            ]
            # 走 run_tracked_ff——cancel_render 可 terminate 在跑的 ffmpeg，
            # 且 rc≠0 时如实返回 False（此前恒 True，叠加失败被静默丢弃）。
            r = run_tracked_ff(cmd, capture_output=True, text=False, timeout=1800)
            if r.returncode != 0:
                logger.warning("Hyperframes: 叠加失败 rc=%s: %s", r.returncode, output_path)
                return False
            return True
        except Exception as e:
            logger.warning("Hyperframes: 叠加失败: %s", e)
            return False


# ── 辅助函数 ─────────────────────────────────────────

def _fmt_sec(value: float) -> str:
    """格式化秒数：整数不带小数点，其余去掉多余尾零（滤镜参数安全形式）。"""
    if float(value).is_integer():
        return str(int(value))
    return ("%.6f" % value).rstrip("0").rstrip(".")


def _mov_longer_than(path: str, seconds: float) -> bool:
    """检查视频时长是否超过给定秒数（用于决定是否需要截断覆盖层）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False
        dur = float(r.stdout.strip().split("\n")[0])
        return dur > seconds + 1e-6
    except Exception:
        return False

def _html_esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def _position_css(position: str) -> str:
    m = {
        "center": "left:50%;top:50%;transform:translate(-50%,-50%)",
        "top": "left:50%;top:20px;transform:translateX(-50%)",
        "bottom": "left:50%;bottom:60px;transform:translateX(-50%)",
        "left": "left:20px;top:50%;transform:translateY(-50%)",
        "right": "right:20px;top:50%;transform:translateY(-50%)",
        "top_left": "left:20px;top:20px",
        "top_right": "right:20px;top:20px",
        "bottom_left": "left:20px;bottom:60px",
        "bottom_right": "right:20px;bottom:60px",
    }
    return m.get(position, m["center"])
