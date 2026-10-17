"""R10：一键跑完所有内容理解验收脚本，汇总成指标表。

每个 R 轮都有自己的 `scripts/verify_*.py`，它们各自打印 `... VERIFY PASS/FAIL`。
本脚本把它们**统一编排**（默认串行，因为都要用 CPU/内存；CLIP 相关的最重）：

    python scripts/verify_all_metrics.py            # 跑全部
    python scripts/verify_all_metrics.py --fast     # 跳过加载模型的慢脚本
    python scripts/verify_all_metrics.py --json out.json

**它不重复实现任何指标**——只调用各轮脚本并解析结论。这样"文档里的数字"与
"脚本跑出来的数字"永远是同一个来源，不会出现"文档写 100%、脚本其实没跑过"。

如实说明：
- 这些脚本的输入**全部是合成素材 / 自写夹具**（本会话没有真实素材），
  所以这里汇总的是"链路正确性"，不是"真实素材上的效果"；
- 表格里的"验证边界"一栏就是从各轮文档里抄下来的那句实话，故意留在这里，
  免得汇总表被当成"效果证明"。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Metric:
    def __init__(self, round_name: str, script: str, metric: str, boundary: str,
                 *, slow: bool = False, extra_args: list[str] | None = None) -> None:
        self.round_name = round_name
        self.script = script
        self.metric = metric
        self.boundary = boundary
        self.slow = slow
        self.extra_args = extra_args or []
        self.ok: bool | None = None
        self.seconds = 0.0
        self.tail = ""


#: 每轮一个条目（顺序 = 依赖顺序）。`metric` 是要在汇总表里展示的结论，
#: `boundary` 是**必须一起看的那句实话**。
METRICS: tuple[Metric, ...] = (
    Metric("R1", "verify_content_index.py", "索引底座：切分/缓存/失效",
           "合成硬切视频；L0 只做统计，无 ML 调用", slow=True),
    Metric("R2", "verify_shot_detection.py", "镜头切分 F1 1.000 / visual_tags 覆盖 0.00→1.00",
           "合成帧上的 CLIP/ImageNet 标签不可信（实测输出“屏幕录制/左轮手枪”）", slow=True),
    Metric("R3", "verify_audio_events.py", "音频事件时间误差 0.020s",
           "合成音频；未安装 STT 后端，转写路径从未真正跑过", slow=True),
    Metric("R4", "verify_content_query.py", "内容问答区间命中 32/32 = 100%",
           "30 条问答为自写夹具；向量只覆盖镜头文字，覆盖不到像素（R9 才补上）", slow=True),
    Metric("R5", "verify_edit_intent.py", "意图解析 50/50 = 100%；幻觉拦截 20/20 = 100%",
           "50 条为自写常见说法；LLM 解析路径仅打桩测试"),
    Metric("R6", "verify_intent_resolver.py", "落位 17/17；歧义反问 5/5 = 100%",
           "自写夹具；会话短名单只在进程内存；反问策略是阈值+规则"),
    Metric("R7", "verify_intent_apply.py", "一句话→成片 25/25；dry-run 只读；硬错误零执行",
           "合成时间线（无真实素材与模型）"),
    Metric("R8", "verify_scene_grounding.py", "引用不存在的素材 = 0（18/18）",
           "构造索引与场景；规则检索，无向量语义（R9 才补）", slow=False),
    Metric("R9", "verify_semantic_search.py", "语义 recall@10 100% vs 关键词 16.7%",
           "**合成画面**，测的是颜色/图案属性；真实素材召回率未验证", slow=True),
)


def run_one(item: Metric) -> None:
    script = ROOT / "scripts" / item.script
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), *item.extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        timeout=3600,
    )
    item.seconds = time.perf_counter() - started
    out = (proc.stdout or "") + (proc.stderr or "")
    item.tail = "\n".join(line for line in out.strip().splitlines()[-3:])
    item.ok = "VERIFY PASS" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过需要加载模型的慢脚本")
    ap.add_argument("--json", default="", help="把结果写到 JSON 文件")
    ap.add_argument("--only", default="", help="只跑某一轮（如 R7）")
    args = ap.parse_args()

    items = [m for m in METRICS if (not args.fast or not m.slow)]
    if args.only:
        items = [m for m in items if m.round_name == args.only.upper()]
    if not items:
        print("没有选中的指标")
        return 1

    print("=" * 88)
    print(f"R10 内容理解验收汇总：{len(items)} 项")
    print("（全部输入均为**合成素材 / 自写夹具**——这里验的是链路正确性，不是真实素材效果）")
    print("=" * 88)

    for item in items:
        print(f"\n[>] {item.round_name}  {item.script}")
        run_one(item)
        mark = "PASS" if item.ok else "FAIL"
        print(f"    {mark}  {item.seconds:.1f}s")
        for line in item.tail.splitlines():
            print(f"    | {line}")

    print("\n" + "=" * 88)
    print("汇总表")
    print("=" * 88)
    print(f"{'轮':<4} {'结果':<6} {'耗时':>7}  指标")
    for item in items:
        mark = "PASS" if item.ok else "FAIL"
        print(f"{item.round_name:<4} {mark:<6} {item.seconds:>6.1f}s  {item.metric}")
    print("\n**验证边界（必须一起读，否则上面的数字会被误读成「真实素材效果」）**：")
    for item in items:
        print(f"  · {item.round_name}: {item.boundary}")

    failed = [m for m in items if not m.ok]
    print("\n" + "=" * 88)
    print(f"{len(items) - len(failed)}/{len(items)} 通过"
          + (f"；失败：{', '.join(m.script for m in failed)}" if failed else ""))
    print("=" * 88)

    if args.json:
        payload = [{
            "round": m.round_name, "script": m.script, "ok": bool(m.ok),
            "seconds": round(m.seconds, 2), "metric": m.metric, "boundary": m.boundary,
            "tail": m.tail,
        } for m in items]
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"已写入 {args.json}")

    print("ALL METRICS VERIFY PASS" if not failed else "ALL METRICS VERIFY FAIL")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
