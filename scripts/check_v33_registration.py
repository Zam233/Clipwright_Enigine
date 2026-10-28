"""V33 真机取证：用已知真值的剪影对，量配准偏差是否 ≤1px。

**素材说明**：这里用的是**合成剪影**（由版画库的画法生成，缩放/位移真值已知），
不是真实拍摄素材——合成对的好处是"真值精确"，能真正量出精度；坏处是不覆盖真实素材的
光照/姿态差异。真实素材的鲁棒性仍属人工项（V41/V16 那一类）。

用法::

    python scripts/check_v33_registration.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CANVAS = (700, 900)
#: 剪影本身画在画布内的一块区域，四周留够余量——否则探针图的位移会把主体裁掉，
#: 质心/面积都会失真（第一版就是踩了这个，误差看着像 1.7px，其实是边界裁剪）
FIGURE_BOX = (200, 250, 500, 650)
CASES: list[tuple[str, float, int, int]] = [
    ("cat_bust", 1.00, -28, -49),
    ("boy_bust", 0.89, 60, -18),
    ("man_bust", 0.873, 81, -4),
]


def _silhouette(size: tuple[int, int]):
    """画一张"胸像"剪影（头 + 肩），并放到画布中央的 FIGURE_BOX 区域。"""
    from PIL import Image, ImageDraw

    box_w = FIGURE_BOX[2] - FIGURE_BOX[0]
    box_h = FIGURE_BOX[3] - FIGURE_BOX[1]
    figure = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(figure)
    head_r = min(box_w, box_h) * 0.22
    cx, cy = box_w * 0.5, box_h * 0.34
    draw.ellipse((cx - head_r, cy - head_r, cx + head_r, cy + head_r), fill=(40, 30, 24, 255))
    draw.ellipse((box_w * 0.12, box_h * 0.52, box_w * 0.88, box_h * 1.02),
                 fill=(40, 30, 24, 255))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(figure, (FIGURE_BOX[0], FIGURE_BOX[1]))
    return canvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V33：剪影配准偏差检查")
    parser.parse_args(argv)

    from clipwright.animation.plate.registration import Registration, apply_registration
    from tools.registration_check import compare_to_truth, estimate_registration, save_report

    base = _silhouette(CANVAS)
    rows = []
    for name, scale, tx, ty in CASES:
        truth = Registration(scale=scale, tx=tx, ty=ty)
        probe = apply_registration(base, truth, CANVAS)
        estimate = estimate_registration(base, probe)
        result = compare_to_truth(estimate, truth)
        result["case"] = name
        rows.append(result)

    report = {
        "ok": all(row["ok"] for row in rows),
        "canvas": list(CANVAS),
        "material": "synthetic（版画库画法生成，真值已知）",
        "caveat": "合成剪影验证精度；真实素材的光照/姿态鲁棒性未覆盖（属人工项）",
        "cases": rows,
    }
    target = save_report(report, ROOT / "out" / "v33" / "report.json")
    print(json.dumps({**report, "report_path": str(target.relative_to(ROOT))},
                     ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
