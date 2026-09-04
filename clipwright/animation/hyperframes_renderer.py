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
from clipwright.config import logger
from clipwright.services.async_util import cached_probe


def _probe_hyperframes() -> bool:
    """同步阻塞探测（在后台线程里跑，绝不进事件循环线程）。"""
    try:
        npx = HyperframesRenderer._npx_cmd()
        # 冷启动 npx 首次解析全局包可能超过 15s（下载/校验），放宽到 90s
        r = subprocess.run([npx, "hyperframes", "--version"],
                           capture_output=True, text=False, timeout=90)
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

            cmd = [
                HyperframesRenderer._npx_cmd(), "hyperframes", "render",
                str(work_dir), "-o", str(out),
                "--format", "mov", "-f", str(int(fps)), "--quiet",
            ]
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

        js = HyperframesRenderer._timing_js()

        return f"""<!DOCTYPE html>
<html data-fps="{int(fps)}" data-width="{width}" data-height="{height}">
<head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{width}px;height:{height}px;overflow:hidden;background:transparent;position:relative}}
.hf-el{{position:absolute;visibility:hidden;animation-fill-mode:forwards}}
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
                # SVG 图解自带逐元素入场动画，不通过 HF JS 控制
                # 隐藏直到 start 时刻，然后由 CSS animation 控制可见性
                return (
                    f'<div class="hf-el hf-diagram-svg" data-i="d{index}" data-start="{start}" '
                    f'data-dur="{dur}" data-anim-class="hf-diagram-reveal" data-anim-dur="0.01" '
                    f'style="position:absolute;top:0;left:0;width:{width}px;height:{height}px">'
                    f'{svg}</div>'
                )

        # 位置 CSS
        pos_css = _position_css(position)

        # 字幕用黑色半透明背景
        is_caption = ov.get("category") == "caption"
        bg_css = "background:rgba(0,0,0,0.55);padding:8px 20px;border-radius:6px" if is_caption else ""

        return (
            f'<div class="hf-el" data-i="{index}" data-start="{start}" '
            f'data-dur="{dur}" data-anim-class="{anim_class}" '
            f'data-anim-dur="{anim_duration}" '
            f'style="font-size:{font_size}px;color:{font_color};'
            f'{pos_css};{bg_css}">{_html_esc(text)}</div>'
        )

    @staticmethod
    def _timing_js() -> str:
        """JavaScript 时序控制：入场动画 → 保持 → 出场淡出。"""
        return """(function(){
const els=document.querySelectorAll('.hf-el');
els.forEach(el=>{
  const s=parseFloat(el.dataset.start)||0;
  const d=parseFloat(el.dataset.dur)||3;
  const ac=el.dataset.animClass||'hf-fade-in';
  const ad=parseFloat(el.dataset.animDur)||0.5;
  const exitDur=Math.min(0.3,d*0.15);
  const exitStart=Math.max(s, s+d-exitDur);
  function show(){
    el.style.visibility='visible';
    el.style.animation=ac+' '+ad+'s ease-out forwards';
    el.style.animationDelay='0s';
  }
  function exit(){
    el.style.animation='hf-fade-out '+exitDur+'s ease-in forwards';
    el.style.animationDelay='0s';
  }
  if(s<=0){show();}else{setTimeout(show,s*1000);}
  if(d>0.5){setTimeout(exit,exitStart*1000);}
});
})();"""

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
            from clipwright.services.render import _resolve_encoder, _hwaccel_args
            encoder = _resolve_encoder()
            hwaccel = _hwaccel_args(encoder)

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
                "-c:v", encoder, "-pix_fmt", "yuv420p",
                "-c:a", "copy", output_path,
            ]
            # 直接 subprocess.run（带超时防挂起）——不用 run_tracked_ff/check：
            # 渲染失败时返回 False 由调用方回退，不中断整体导出。
            subprocess.run(
                cmd, capture_output=True, text=False, timeout=1800,
            )
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
