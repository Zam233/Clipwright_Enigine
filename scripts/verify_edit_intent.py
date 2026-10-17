"""R5 验收：NL → 意图解析正确率 ≥ 85%，幻觉操作拒绝率 100%。

两部分：

- **解析准确率**：50 条中文原话（覆盖约束/删除/保留/排序/加法/转场/裁剪/字段设置/组合/长尾），
  逐条比对**期望的 op 与关键字段**。跑的是**确定性规则解析器**（零 token、可回归）——
  LLM 路径只在规则解析不出时才兜底，且其输出同样要过校验。
- **幻觉拒绝**：20 条"系统不支持的操作/字段/取值"（含 LLM 常见编造：`delete_everything`、
  `render_engine`、`speed=1000`、`kind=hologram`…）必须**全部**被判为 error 或安全钳制，
  一条都不能漏到执行阶段。

用法：
    python scripts/verify_edit_intent.py [--min-accuracy 0.85]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipwright.services import edit_intent as ei  # noqa: E402


@dataclass
class Case:
    text: str
    check: Callable[[ei.EditIntent], bool]
    note: str = ""


def _op(op: str, **fields: Any) -> Callable[[ei.EditIntent], bool]:
    def _check(intent: ei.EditIntent) -> bool:
        for candidate in intent.ops:
            if candidate.op != op:
                continue
            ok = True
            for key, value in fields.items():
                actual = getattr(candidate, key, None)
                if key == "target_filter":
                    actual = candidate.target.filter
                elif key == "target_query_has":
                    actual = candidate.target.query
                    ok = ok and isinstance(actual, str) and value in actual
                    continue
                if isinstance(value, float) and isinstance(actual, (int, float)):
                    ok = ok and abs(float(actual) - value) < 1e-6
                else:
                    ok = ok and actual == value
            if ok:
                return True
        return False
    return _check


def _cons(**fields: Any) -> Callable[[ei.EditIntent], bool]:
    def _check(intent: ei.EditIntent) -> bool:
        return all(getattr(intent.constraints, k) == v for k, v in fields.items())
    return _check


def _unresolved(intent: ei.EditIntent) -> bool:
    return bool(intent.unresolved)


CASES: list[Case] = [
    # ── 约束（8）──
    # 注意：检查跑在**校验后**的意图上，因此画幅已归一化为 landscape/vertical/square
    Case("剪到 60 秒", _cons(total_duration_sec=60.0), "总时长"),
    Case("控制在四十五秒以内", _cons(total_duration_sec=45.0), "中文数字"),
    Case("总时长要 90 秒", _cons(total_duration_sec=90.0), "总时长（口语插入词）"),
    Case("剪到 120 秒", _cons(total_duration_sec=120.0), "总时长"),
    Case("换成竖屏", _cons(platform="vertical"), "画幅（归一化）"),
    Case("做成 16:9", _cons(platform="landscape"), "画幅（宽高比写法）"),
    Case("同一素材只用一次", _cons(no_reuse=True), "不重复素材"),
    Case("剪到 30 秒，换成竖屏", _cons(total_duration_sec=30.0, platform="vertical"), "组合约束"),
    # ── 删除（9）──
    Case("去掉黑场", _op("remove", target_filter={"is_black": True}), "结构化条件"),
    Case("把黑场删掉", _op("remove", target_filter={"is_black": True}), "把字句"),
    Case("删掉全白的画面", _op("remove", target_filter={"is_white": True}), "白场"),
    Case("把掌声那几段删掉", _op("remove", target_filter={"event_kinds": ["loud"]}), "音频事件"),
    Case("去掉静音的部分", _op("remove", target_filter={"event_kinds": ["silence"]}), "静音"),
    Case("别用糊的镜头", _op("remove", target_filter={"max_sharpness": 120.0}), "模糊"),
    Case("把没人物的空镜去掉", _op("remove", target_filter={"has_face": False}), "空镜"),
    Case("删掉过曝的镜头", _op("remove", target_filter={"min_overexposed": 0.3}), "过曝"),
    Case("把这一段删掉", _op("remove"), "把字句（弱条件）"),
    # ── 保留（6）──
    Case("只保留有人脸的镜头", _op("keep", target_filter={"has_face": True}), "结构化条件"),
    Case("只留下特写", _op("keep", target_filter={"shot_size": ["extreme_close", "close"]}), "景别"),
    Case("只要全景", _op("keep", target_filter={"shot_size": ["wide", "full"]}), "景别"),
    Case("只保留咖啡杯特写", _op("keep", target_query_has="咖啡杯"), "内容查询"),
    Case("只留下有语音的片段", _op("keep", target_filter={"has_speech": True}), "语音"),
    Case("只保留运动剧烈的镜头", _op("keep", target_filter={"motion": ["medium", "high"]}), "运动"),
    # ── 排序（6）──
    Case("按运动量从大到小重排", _op("reorder", by="motion", desc=True), "排序+方向"),
    Case("按时长从小到大排序", _op("reorder", by="duration", desc=False), "升序"),
    Case("按照亮度降序重排", _op("reorder", by="brightness", desc=True), "亮度"),
    Case("按清晰度排", _op("reorder", by="sharpness"), "清晰度"),
    Case("把这些片段按运动量从大到小排", _op("reorder", by="motion"), "口语说法"),
    Case("按时间重排", _op("reorder", by="time"), "时间"),
    # ── 加法（7）──
    Case("给开头加个字幕", _op("add", kind="caption", position="start"), "字幕+位置"),
    Case("结尾加个背景音乐", _op("add", kind="audio", position="end"), "音乐+位置"),
    Case("加个标题", _op("add", kind="text"), "标题"),
    Case("添加动画", _op("add", kind="animation"), "动画"),
    Case("来点音效", _op("add", kind="audio"), "音效"),
    Case("片头放上标题", _op("add", kind="text", position="start"), "片头"),
    Case("加个贴纸", _op("add", kind="image"), "贴纸"),
    # ── 转场 / 裁剪 / 字段（8）──
    Case("转场改成淡入淡出", _op("transition", transition="淡入淡出"), "转场名"),
    Case("加转场", _op("transition"), "默认转场"),
    Case("把这段裁到 3 秒", _op("trim", duration_sec=3.0), "裁剪"),
    Case("把选中的速度调到 1.5 倍", _op("set", field="speed", value=1.5), "速度"),
    Case("加速到 2x", _op("set", field="speed", value=2.0), "倍速后缀"),
    Case("音量调大一点", _op("set", field="volume"), "音量"),
    Case("音量调小一点", _op("set", field="volume"), "音量"),
    Case("把速度调到两倍", _op("set", field="speed", value=2.0), "中文数字倍速"),
    # ── 组合与长尾（6）──
    Case("去掉黑场，剪到 60 秒", lambda i: bool(i.ops) and i.constraints.total_duration_sec == 60.0,
         "组合"),
    Case("只保留有人脸的镜头并换成竖屏",
         lambda i: bool(i.ops) and i.constraints.platform == "vertical", "组合"),
    Case("随便剪一下", _unresolved, "长尾 → 进 unresolved（不猜）"),
    Case("让它更好看一点", _unresolved, "长尾"),
    Case("帮我处理一下素材", _unresolved, "长尾"),
    Case("剪得紧凑一些", _unresolved, "长尾"),
]

# ── 幻觉拒绝：必须全部被拦住 ──────────────────────────────

HALLUCINATIONS: list[tuple[str, dict[str, Any]]] = [
    ("不存在的 op", {"op": "delete_everything"}),
    ("不存在的 op 2", {"op": "ai_magic_edit"}),
    ("不存在的 op 3", {"op": "publish_to_youtube"}),
    ("不存在的字段", {"op": "set", "target": {"all": True}, "field": "render_engine", "value": "ffmpeg"}),
    ("不存在的字段 2", {"op": "set", "target": {"all": True}, "field": "watermark_text", "value": "x"}),
    ("不存在的字段 3", {"op": "set", "target": {"all": True}, "field": "__class__", "value": "x"}),
    ("超范围倍速", {"op": "set", "target": {"all": True}, "field": "speed", "value": 1000}),
    ("负音量", {"op": "set", "target": {"all": True}, "field": "volume", "value": -5}),
    ("非数值", {"op": "set", "target": {"all": True}, "field": "speed", "value": "飞快"}),
    ("非法颜色", {"op": "set", "target": {"all": True}, "field": "font_color", "value": "red"}),
    ("非法枚举", {"op": "set", "target": {"all": True}, "field": "image_fit", "value": "stretch"}),
    ("非法遮罩", {"op": "set", "target": {"all": True}, "field": "mask_type", "value": "hexagon"}),
    ("结构化操作缺对象", {"op": "remove"}),
    ("保留缺对象", {"op": "keep"}),
    ("裁剪缺对象", {"op": "trim", "duration_sec": 3}),
    ("时间区间倒置", {"op": "remove", "target": {"time_range": [10, 2]}}),
    ("非法重排依据", {"op": "reorder", "by": "brainwaves"}),
    ("非法新增类型", {"op": "add", "kind": "hologram"}),
    ("负总时长", {"op": "reorder", "constraints": {"total_duration_sec": -10}}),
    ("非法画幅", {"op": "reorder", "constraints": {"platform": "球形"}}),
]


def _intent_from(payload: dict[str, Any]) -> ei.EditIntent:
    data = dict(payload)
    data.pop("constraints", None)
    constraints = payload.get("constraints")
    try:
        return ei.EditIntent(goal="fixture", ops=[ei.IntentOp(**data)],
                             constraints=ei.IntentConstraints(**(constraints or {})))
    except Exception:
        return ei.EditIntent(goal="fixture-broken")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-accuracy", type=float, default=0.85)
    args = ap.parse_args()

    print("=" * 72)
    print("第一部分：解析准确率（确定性规则解析器，零 token）")
    print("=" * 72)
    failures: list[tuple[str, str]] = []
    for case in CASES:
        intent, problems = ei.validate_intent(ei.parse_intent_rules(case.text))
        ok = False
        try:
            ok = bool(case.check(intent))
        except Exception as exc:  # 夹具写错也要看得见
            failures.append((case.text, f"检查器异常 {exc}"))
            print(f"  [ERR ] {case.text:32s} {case.note}")
            continue
        print(f"  [{'OK ' if ok else 'MISS'}] {case.text:32s} {case.note}")
        if not ok:
            failures.append((case.text, ei.describe_intent(intent).replace("\n", " | ")))

    total = len(CASES)
    passed = total - len(failures)
    accuracy = passed / total
    print(f"\n  解析正确 {passed}/{total} = {accuracy * 100:.1f}%"
          f"（门槛 ≥{args.min_accuracy * 100:.0f}%）")
    if failures:
        print("  未通过明细：")
        for text, detail in failures:
            print(f"    · {text} → {detail[:110]}")

    print()
    print("=" * 72)
    print("第二部分：幻觉拒绝（必须 100%）")
    print("=" * 72)
    leaked: list[str] = []
    for note, payload in HALLUCINATIONS:
        intent = _intent_from(payload)
        norm, problems = ei.validate_intent(intent)
        # 判定：报错 或 值被安全钳制 或 该 op 被剔除；三者必居其一
        clamped = bool(norm.ops) and (
            intent.ops and norm.ops and norm.ops[0].value != intent.ops[0].value)
        rejected = ei.has_errors(problems) or not norm.ops or clamped
        print(f"  [{'OK ' if rejected else 'LEAK'}] {note:16s} "
              f"{'拒绝/钳制' if rejected else '漏到执行阶段'}")
        if not rejected:
            leaked.append(note)

    print(f"\n  拦截 {len(HALLUCINATIONS) - len(leaked)}/{len(HALLUCINATIONS)}")
    if leaked:
        print("  漏网：" + "、".join(leaked))

    print()
    ok = accuracy >= args.min_accuracy and not leaked
    if not ok:
        print("EDIT INTENT VERIFY FAIL")
        return 1
    print("EDIT INTENT VERIFY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
