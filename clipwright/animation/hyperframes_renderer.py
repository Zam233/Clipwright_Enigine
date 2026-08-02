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


class HyperframesRenderer:
    """使用 Hyperframes (HTML→MOV) 渲染文字覆盖层。"""

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
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=False, timeout=3600
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
    ) -> bool:
        """将 HF 输出的 MOV 叠加到主视频。"""
        try:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", main_video, "-i", overlay_video,
                "-filter_complex", "[0:v][1:v]overlay=format=auto[vout]",
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", output_path,
            ]
            subprocess.run(cmd, capture_output=True, text=False, timeout=1800, check=True)
            return True
        except Exception as e:
            logger.warning("Hyperframes: 叠加失败: %s", e)
            return False


# ── 辅助函数 ─────────────────────────────────────────

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
