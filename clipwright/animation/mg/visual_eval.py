"""MG 渲染后视觉自评（P1-1）。

为什么需要它
------------
`MGGenerator` 原有的自批判闭环（`_critique_quality`）只看 **JSON 层面**的设计原则——
它读的是 `mg_def` 结构，看不到真正渲出来的画面。于是"元素压字/超出安全区/对比度不足/
元素堆叠"这类**只有看图才知道**的问题会一路进成片。

本模块补上"眼睛"这一环，形态与 browser-use/video-use（MIT）的渲染后自评闭环一致：
    渲染 → 关键帧截图（hyperframes snapshot） → 视觉模型按评分表打分 →
    低分则把问题清单交给既有的批判修复路径重做一次
（方法论借鉴，未复制其代码；其 `timeline_view` 工具亦未移植。）

成本与门控
----------
每次启用会多一次 `hyperframes snapshot`（headless Chrome，秒级）+ 一次视觉 LLM 调用，
因此**默认关闭**，需同时满足：
1. 配置 `mg/config.yaml → critique.visual_eval.enabled: true`
2. 全局开关 `settings.enable_visual_llm: true`（与素材视觉质检共用，便于统一开关与计费）

失败一律静默跳过（CLI 不可用 / 未配置视觉模型 / 返回非 JSON），绝不阻断生成。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clipwright.config import logger

# 评分表：每项都要能被"从截图看出来"，避免模型空泛打分。
_RUBRIC = """你是短视频动效（Motion Graphics）质检员。图是该动画在时间轴上的若干关键帧截图
（按顺序给出，背景透明处以 checkerboard/黑底呈现）。请只依据**画面本身**评分。

评分维度（各 20 分，总分 100）：
1. 可读性：文字是否清晰、字号是否足够、是否被其他元素压住或与背景对比过低
2. 视觉层级：主体是否突出，陪衬元素是否克制（同屏同时可见元素建议 ≤ 6 个）
3. 构图与安全区：是否预留留白（建议 ≥15%），元素是否贴边/越界/相互重叠
4. 一致性：配色与字体是否统一（不应出现多个相近却不同的主色），风格是否与给定期望一致
5. 完成度：是否存在空帧、元素残缺、明显错位、占位符/乱码残留

只输出 JSON（不要 markdown 代码块、不要额外解释）：
{"score": <0-100 整数>, "issues": [{"severity": "high|medium|low", "detail": "<具体到画面位置>"}],
 "suggestions": ["<可执行的修改建议>"], "summary": "<一句话结论>"}
没有任何问题时 issues 为空数组且 score >= 80。"""


def _default_sample_times(duration: float) -> list[float]:
    """默认采样点：入场结束附近 / 中段 / 退场前（覆盖"亮相-保持-收尾"三段）。"""
    dur = max(0.5, float(duration or 3.0))
    enter = min(1.0, dur * 0.35)
    return [
        round(enter, 2),
        round(dur * 0.5, 2),
        round(max(0.0, dur - 0.3), 2),
    ]


class MGVisualEvaluator:
    """把 MG HTML 渲成关键帧截图，交视觉 LLM 打分。"""

    def __init__(self, cli_runner=None) -> None:
        # cli_runner: 可选注入（签名 (cmd, env, timeout) -> CompletedProcess 风格）
        self._cli_runner = cli_runner

    # ── 截图 ──────────────────────────────────────────────

    async def snapshot_html(
        self,
        html: str,
        *,
        sample_times: list[float] | None = None,
        duration: float = 3.0,
        timeout: float = 240.0,
    ) -> list[str]:
        """把合成 HTML 渲成关键帧 PNG，返回文件路径列表（失败返回 []）。"""
        if not html:
            return []
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer

        times = sample_times or _default_sample_times(duration)
        work = Path(tempfile.mkdtemp(prefix="mg_veval_"))
        out_dir = work / "frames"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            (work / "index.html").write_text(html, encoding="utf-8")
            cmd = HyperframesRenderer._cli_cmd(
                "snapshot", str(work),
                "--at", ",".join(str(t) for t in times),
                "-o", str(out_dir),
            )
            env = HyperframesRenderer._render_env()
            if self._cli_runner is not None:
                await self._cli_runner(cmd, env, timeout)
            else:
                await asyncio.to_thread(
                    subprocess.run, cmd, capture_output=True, text=False,
                    timeout=timeout, env=env,
                )
            pngs = sorted(str(p) for p in out_dir.glob("*.png"))
            if not pngs:
                logger.warning("MGVisualEvaluator: snapshot 未产出帧（times=%s）", times)
                # 无帧 → 中间产物无用，立即清理
                import shutil

                shutil.rmtree(work, ignore_errors=True)
            return pngs
        except Exception as exc:  # best-effort：视觉自评不得阻断生成
            logger.warning("MGVisualEvaluator: snapshot 失败: %s", exc)
            import shutil

            shutil.rmtree(work, ignore_errors=True)
            return []
        # 注意：成功时**不删**临时目录——PNG 由调用方读取，
        # evaluate_html() 会在用完后清理；单独调用本方法时请自行清理返回值所在目录。

    # ── 打分 ──────────────────────────────────────────────

    async def evaluate(
        self,
        image_paths: list[str],
        *,
        mg_def: dict[str, Any] | None = None,
        description: str = "",
        persona_style: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any] | None:
        """视觉 LLM 打分。未配置/调用失败/非 JSON → 返回 None（调用方跳过）。"""
        if not image_paths:
            return None
        try:
            from clipwright.config import settings
            if not getattr(settings, "enable_visual_llm", False):
                logger.debug("MGVisualEvaluator: enable_visual_llm 关闭，跳过视觉自评")
                return None
            from clipwright.services.llm import LLMService

            client = LLMService.build_client(
                provider=settings.vision_llm_provider or settings.llm_provider,
                api_key=settings.vision_llm_api_key or settings.llm_api_key,
                model=settings.vision_llm_model or settings.llm_model,
                base_url=settings.vision_llm_base_url or settings.llm_base_url or "",
                role="vision",
            )
            provider = settings.vision_llm_provider or settings.llm_provider
        except Exception as exc:
            logger.warning("MGVisualEvaluator: 视觉 LLM 不可用: %s", exc)
            return None

        context = self._build_context(mg_def, description, persona_style, width, height)
        content: list[dict[str, Any]] = [{"type": "text", "text": _RUBRIC + context}]
        for p in image_paths[:5]:
            try:
                with open(p, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("utf-8")
            except Exception:
                continue
            if provider == "anthropic":
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                })
            else:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
        if len(content) == 1:
            return None

        messages = [{"role": "user", "content": content}]
        try:
            resp = await asyncio.to_thread(client.generate, messages=messages)
            if not getattr(resp, "success", False):
                logger.warning("MGVisualEvaluator: 视觉 LLM 调用失败: %s",
                               getattr(resp, "content", "")[:200])
                return None
            parsed = self._parse_json(getattr(resp, "content", "") or "")
        except Exception as exc:
            logger.warning("MGVisualEvaluator: 视觉 LLM 异常: %s", exc)
            return None
        if parsed is None:
            return None
        return self._normalize(parsed, image_paths)

    @staticmethod
    def _build_context(
        mg_def: dict[str, Any] | None,
        description: str,
        persona_style: dict[str, Any] | None,
        width: int | None,
        height: int | None,
    ) -> str:
        parts: list[str] = []
        if description:
            parts.append(f"\n镜头意图：{description[:300]}")
        style = (mg_def or {}).get("style") or {}
        expected: list[str] = []
        if style.get("font_family"):
            expected.append(f"字体 {style['font_family']}")
        ps = persona_style or {}
        for key, label in (
            ("primary_color", "主色"), ("secondary_color", "辅色"), ("accent_color", "强调色"),
        ):
            if ps.get(key):
                expected.append(f"{label} {ps[key]}")
        if expected:
            parts.append("\n期望的视觉令牌（用于一致性判断）：" + "、".join(expected))
        if width and height:
            parts.append(f"\n画布：{width}x{height}（元素不得贴边或越界）")
        parts.append(
            "\n注意：该动画是**叠加在实拍视频上的透明层**，除了元素本身外画面应为透明；"
            "若出现整幅不透明底色，判为严重问题。"
        )
        return "".join(parts)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any] | None:
        text = content.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.startswith("```"))
        # 容错：截取首个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _normalize(parsed: dict[str, Any], image_paths: list[str]) -> dict[str, Any]:
        try:
            score = int(float(parsed.get("score", 0)))
        except (TypeError, ValueError):
            score = 0
        issues_raw = parsed.get("issues") or []
        issues: list[dict[str, str]] = []
        for item in issues_raw if isinstance(issues_raw, list) else []:
            if isinstance(item, dict):
                issues.append({
                    "severity": str(item.get("severity", "medium")),
                    "detail": str(item.get("detail", "")),
                })
            elif isinstance(item, str):
                issues.append({"severity": "medium", "detail": item})
        suggestions = [
            str(s) for s in (parsed.get("suggestions") or []) if isinstance(s, (str, int, float))
        ]
        return {
            "score": max(0, min(100, score)),
            "issues": issues,
            "suggestions": suggestions,
            "summary": str(parsed.get("summary", "")),
            "frames": list(image_paths),
            "source": "visual_eval",
        }

    # ── 一站式入口 ────────────────────────────────────────

    async def evaluate_html(
        self,
        html: str,
        *,
        mg_def: dict[str, Any] | None = None,
        description: str = "",
        persona_style: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: float = 3.0,
        sample_times: list[float] | None = None,
    ) -> dict[str, Any] | None:
        """截图 + 打分。返回 None 表示跳过（未配置/不可用/失败）。"""
        import shutil

        pngs = await self.snapshot_html(
            html, sample_times=sample_times, duration=duration,
        )
        if not pngs:
            return None
        try:
            result = await self.evaluate(
                pngs, mg_def=mg_def, description=description,
                persona_style=persona_style, width=width, height=height,
            )
        finally:
            for p in pngs:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            try:
                shutil.rmtree(Path(pngs[0]).parent.parent, ignore_errors=True)
            except OSError:
                pass
        return result
