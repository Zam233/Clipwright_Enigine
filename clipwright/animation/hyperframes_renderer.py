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

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clipwright.animation.catalog import AnimationCatalog
from clipwright.config import logger


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
        try:
            npx = HyperframesRenderer._npx_cmd()
            r = subprocess.run([npx, "hyperframes", "--version"],
                               capture_output=True, text=False, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def render_overlays(
        overlays: list[dict[str, Any]],
        output_path: str,
        width: int = 1920, height: int = 1080, fps: float = 30.0,
    ) -> str | None:
        """将所有 overlay 渲染为透明 MOV。"""
        if not overlays:
            return None
        if not HyperframesRenderer.is_available():
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
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=600)
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

        # 逻辑图解 → SVG
        if diagram_params:
            return HyperframesRenderer._diagram_svg(
                text, diagram_params, start, dur, font_size, font_color,
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
    def _diagram_svg(
        text: str, params: dict, start: float, dur: float,
        font_size: int, font_color: str,
        width: int = 1920, height: int = 1080,
    ) -> str:
        """逻辑图解 → 行内 SVG。"""
        preset = params.get("preset", "diagram")
        items = params.get("items", [])
        title = params.get("title", "")
        cx, cy = 960, 400  # 中心坐标

        # 箭头图解
        if preset in ("diagram", "causation"):
            n = min(len(items), 5)
            spacing = 260
            total_w = (n - 1) * spacing + 200
            sx = cx - total_w // 2
            rects, labels, arrows = "", "", ""
            for i, item in enumerate(items[:5]):
                x = sx + i * spacing
                rects += f'<rect x="{x}" y="{cy-25}" width="180" height="50" rx="10" fill="rgba(255,255,255,0.12)"/>'
                labels += f'<text x="{x+90}" y="{cy+5}" font-size="{font_size-8}px" fill="{font_color}" text-anchor="middle">{_html_esc(item[:20])}</text>'
                if i < n - 1:
                    ax = x + 180
                    ay = cy
                    arrows += f'<line x1="{ax}" y1="{ay}" x2="{ax+spacing-180}" y2="{ay}" stroke="#4f8cff" stroke-width="3" marker-end="url(#a)"/>'
                    arrows += f'<text x="{ax+(spacing-180)//2}" y="{ay-8}" font-size="20" fill="#4f8cff" text-anchor="middle">→</text>'
            title_svg = f'<text x="{cx}" y="{cy-60}" font-size="{font_size}px" fill="{font_color}" text-anchor="middle" font-weight="bold">{_html_esc(title[:60])}</text>' if title else ""
            return (
                f'<svg class="hf-el" data-i="d{start}" data-start="{start}" data-dur="{dur}"'
                f' data-anim-class="hf-fade-in" data-anim-dur="0.5"'
                f' width="{width}" height="{height}" style="position:absolute;top:0;left:0">'
                f'<defs><marker id="a" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0L10,5L0,10Z" fill="#4f8cff"/></marker></defs>'
                f'{title_svg}{rects}{arrows}{labels}</svg>'
            )

        # 对比图解
        elif preset == "comparison":
            left = items[0] if items else ""
            right = items[1] if len(items) > 1 else ""
            return (
                f'<svg class="hf-el" data-i="c{start}" data-start="{start}" data-dur="{dur}"'
                f' data-anim-class="hf-fade-in" data-anim-dur="0.5"'
                f' width="{width}" height="{height}" style="position:absolute;top:0;left:0">'
                f'<rect x="{cx-260}" y="{cy-30}" width="220" height="60" rx="12" fill="rgba(255,255,255,0.12)"/>'
                f'<text x="{cx-150}" y="{cy+6}" font-size="{font_size-6}px" fill="{font_color}" text-anchor="middle">{_html_esc(left[:20])}</text>'
                f'<text x="{cx}" y="{cy+6}" font-size="36" fill="#ff6b6b" text-anchor="middle" font-weight="bold">VS</text>'
                f'<rect x="{cx+40}" y="{cy-30}" width="220" height="60" rx="12" fill="rgba(255,255,255,0.12)"/>'
                f'<text x="{cx+150}" y="{cy+6}" font-size="{font_size-6}px" fill="{font_color}" text-anchor="middle">{_html_esc(right[:20])}</text>'
                f'</svg>'
            )

        # 流程图解
        elif preset == "sequence":
            items_html = []
            for i, item in enumerate(items[:5], 1):
                yy = cy - 80 + i * 40
                items_html.append(
                    f'<text x="{cx}" y="{yy}" font-size="{font_size-6}px" fill="{font_color}" '
                    f'text-anchor="middle">{i}. {_html_esc(item[:30])}</text>'
                )
            title_html = (
                f'<text x="{cx}" y="{cy-120}" font-size="{font_size}px" fill="{font_color}" '
                f'text-anchor="middle" font-weight="bold">{_html_esc(title[:60])}</text>'
            ) if title else ""
            return (
                f'<svg class="hf-el" data-i="s{start}" data-start="{start}" data-dur="{dur}"'
                f' data-anim-class="hf-fade-in" data-anim-dur="0.5"'
                f' width="{width}" height="{height}" style="position:absolute;top:0;left:0">'
                f'{title_html}{"".join(items_html)}</svg>'
            )

        # fallback
        return (
            f'<div class="hf-el" data-i="f{start}" data-start="{start}" '
            f'data-dur="{dur}" data-anim-class="hf-fade-in" data-anim-dur="0.5" '
            f'style="left:50%;top:50%;transform:translate(-50%,-50%);'
            f'font-size:{font_size}px;color:{font_color}">{_html_esc(text)}</div>'
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
            subprocess.run(cmd, capture_output=True, text=False, timeout=300, check=True)
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
