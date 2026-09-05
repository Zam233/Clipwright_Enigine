"""关键帧 → FFmpeg 表达式（V3：预览所见 = 导出所得）。

时间基契约——后端存在两个关键帧生产者，时间基不同：
- 前端 exportTimeline 导出：time = 片段内相对秒（归一化 0-1 × duration），
  且 clip.metadata.kf_time_base = "clip_local" 显式标记；
- 管线 AnimationCatalog.build_full_keyframes：time = 时间轴绝对秒（无标记）。

normalize_keyframe_times 统一到目标时间基：标记优先，无标记时启发式判定
（min(time) >= start_sec - eps → 视为绝对秒；否则视为片段相对秒）。

easing：与前端 easing.ts 同名的 Penner 缓动映射为 FFmpeg 表达式（u 为段内
进度）。未知名字回退 linear。表达式以 t 为自变量（消费方时间基）。
"""

from __future__ import annotations

from typing import Any

_EPS = 0.05

# 缓动名 → u 表达式模板（与前端 easing.ts 公式一一对应）
_EASE_IN_BACK_C = 1.70158
_EASE_IN_BACK_C1 = 1.70158 + 1
_EASE_IN_OUT_BACK_C = 1.70158 * 1.525
_EASE_IN_OUT_BACK_C1 = _EASE_IN_OUT_BACK_C + 1

EASING_EXPR: dict[str, str] = {
    "linear": "({u})",
    "ease-in": "pow({u},2)",
    "ease-in-quad": "pow({u},2)",
    "ease-out": "({u})*(2-({u}))",
    "ease-out-quad": "({u})*(2-({u}))",
    "ease-in-out": "if(lt({u},0.5),2*pow({u},2),-1+(4-2*({u}))*({u}))",
    "ease-in-out-quad": "if(lt({u},0.5),2*pow({u},2),-1+(4-2*({u}))*({u}))",
    "ease-in-cubic": "pow({u},3)",
    "ease-out-cubic": "1+pow(({u})-1,3)",
    "ease-in-out-cubic": "if(lt({u},0.5),4*pow({u},3),1+4*pow(({u})-1,3))",
    "ease-in-quart": "pow({u},4)",
    "ease-out-quart": "1-pow(({u})-1,4)",
    "ease-in-out-quart": "if(lt({u},0.5),8*pow({u},4),1-8*pow(({u})-1,4))",
    "ease-in-expo": "pow(2,10*({u})-10)",
    "ease-out-expo": "1-pow(2,-10*({u}))",
    "ease-in-out-expo": "if(lt({u},0.5),pow(2,20*({u})-10)/2,(2-pow(2,-20*({u})+10))/2)",
    "ease-in-back": (
        f"{_EASE_IN_BACK_C1:.5f}*pow({{u}},3)-{_EASE_IN_BACK_C:.5f}*pow({{u}},2)"
    ),
    "ease-out-back": (
        f"1+{_EASE_IN_BACK_C1:.5f}*pow(({{u}})-1,3)+{_EASE_IN_BACK_C:.5f}*pow(({{u}})-1,2)"
    ),
    "ease-in-out-back": (
        f"if(lt({{u}},0.5),(pow(2*({{u}}),2)*({_EASE_IN_OUT_BACK_C1:.5f}*2*({{u}})-{_EASE_IN_OUT_BACK_C:.5f}))/2,"
        f"(pow(2*({{u}})-2,2)*({_EASE_IN_OUT_BACK_C1:.5f}*(2*({{u}})-2)+{_EASE_IN_OUT_BACK_C:.5f})+2)/2)"
    ),
    "ease-out-elastic": "pow(2,-10*({u}))*sin((10*({u})-0.75)*(2*PI/3))+1",
    # 前端分段抛物线弹跳（d1=2.75, n1=7.5625）
    "ease-out-bounce": (
        "if(lt({u},0.363636),7.5625*pow({u},2),"
        "if(lt({u},0.727273),7.5625*pow(({u})-0.545455,2)+0.75,"
        "if(lt({u},0.909091),7.5625*pow(({u})-0.818182,2)+0.9375,"
        "7.5625*pow(({u})-0.954545,2)+0.984375)))"
    ),
}


def easing_u(name: str | None, u: str) -> str:
    """段内进度表达式 u 经缓动映射；未知/缺失名字回退 linear。"""
    tpl = EASING_EXPR.get(str(name or "linear").lower(), EASING_EXPR["linear"])
    return tpl.format(u=u)


def _num(v: Any) -> str:
    """数值 → ffmpeg 表达式字面量（定点格式，禁科学计数法——ffmpeg 不接受 e 记法）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def normalize_keyframe_times(
    keyframes: list[dict] | None,
    duration: float,
    start_sec: float,
    time_base_marker: str | None = None,
) -> list[dict]:
    """把关键帧时间归一化到「片段内相对秒」并排序。

    判定顺序：显式标记（kf_time_base）> 启发式。启发式：
    - start_sec <= 0：两种时间基等价，无需换算；
    - min(time) >= start_sec - eps：关键帧整体位于片段窗口后部 → 绝对秒，减 start_sec；
    - 否则：片段相对秒，保持。

    Args:
        duration: 片段时长（秒）
        start_sec: 片段在时间轴上的起点（秒）
        time_base_marker: "clip_local"（前端标记）| "absolute" | None
    """
    if not keyframes:
        return []
    times = []
    for kf in keyframes:
        try:
            times.append(float(kf.get("time", 0)))
        except (TypeError, ValueError):
            times.append(0.0)
    base = time_base_marker or "unknown"
    if base == "unknown":
        if start_sec > _EPS and times and min(times) >= start_sec - _EPS:
            base = "absolute"
        else:
            base = "clip_local"
    shift = 0.0 if base == "clip_local" else -float(start_sec)
    out = []
    for kf in sorted(
        [k for k in keyframes if isinstance(k, dict)],
        key=lambda k: float(k.get("time", 0) or 0),
    ):
        props = dict(kf.get("properties") or {})
        out.append({
            "time": round(max(0.0, min(duration, float(kf.get("time", 0) or 0) + shift)), 6),
            "properties": props,
            **({"easing": kf["easing"]} if kf.get("easing") else {}),
        })
    return out


def property_expression(
    keyframes: list[dict],
    prop: str,
    default: float,
    easing_default: str = "linear",
    t_offset: float = 0.0,
) -> str | None:
    """构造属性 v(t) 的分段插值 FFmpeg 表达式。

    t_offset：关键帧时间整体平移量——drawtext 路径在成片时间轴上求值
    （t_offset=start_sec），trim 路径在片段输出时间轴上求值（t_offset=0）。

    - <2 个含该属性的关键帧 → None（调用方用 default）；
    - 窗口外钳位到端点值；
    - 段内：v0+(v1-v0)*E((t-t0)/(t1-t0))，E 为该段 easing（起始 kf 携带）；
    - 嵌套构造：if(lt(t,T0),v0, if(lt(t,T1),seg0, ... vn))
    """
    pts: list[tuple[float, float, str | None]] = []
    for kf in keyframes:
        p = kf.get("properties") or {}
        if prop not in p:
            continue
        try:
            pts.append((float(kf.get("time", 0) or 0) + t_offset, float(p[prop]),
                        kf.get("easing") or easing_default))
        except (TypeError, ValueError):
            continue
    if not pts:
        return None
    if len(pts) == 1:
        return _num(pts[0][1])

    # 正向回卷：if(lt(t,T1),seg0, if(lt(t,T2),seg1, ... vn))，
    # 最外层补首帧前钳位：if(lt(t,T0),v0,...)
    expr = _num(pts[-1][1])
    for i in range(len(pts) - 1, 0, -1):
        seg = _segment_expr(pts[i - 1], pts[i])
        expr = f"if(lt(t,{_num(pts[i][0])}),{seg},{expr})"
    return f"if(lt(t,{_num(pts[0][0])}),{_num(pts[0][1])},{expr})"


def _segment_expr(a: tuple[float, float, str | None], b: tuple[float, float, str | None]) -> str:
    t0, v0, _ = a
    t1, v1, easing = b
    span = t1 - t0
    if span <= 1e-6:
        return _num(v1)
    u = f"(t-{_num(t0)})/{_num(span)}"
    return f"{_num(v0)}+(({_num(v1)})-({_num(v0)}))*{easing_u(easing, u)}"
