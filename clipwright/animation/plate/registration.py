"""剪影素材与配准（PRD C8 / R-10）。

参考样本的做法：以 ``f_bust`` 为基准，其他剪影按 ``{scale, tx, ty}`` 对齐
（``cat_bust`` s 1.00 t(−28,−49)；``boy_bust`` s 0.89 t(+60,−18)；
``man_bust`` s 0.873 t(+81,−4)），保证"换一张图不会跳"。

本模块提供：
- ``Registration`` 数据类与 JSON 读写（``assets/character/registration.json``）；
- ``apply_registration``：把素材按配准参数画到目标画布；
- ``derive_size``：按原图实际比例生成衍生尺寸（不再写死 2:3）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


@dataclass(frozen=True)
class Registration:
    """一个素材相对基准图的缩放与位移（单位：原图像素）。"""

    scale: float = 1.0
    tx: int = 0
    ty: int = 0

    def apply(self, width: int, height: int) -> tuple[int, int]:
        return (max(1, int(round(width * self.scale))), max(1, int(round(height * self.scale))))

    def offset(self) -> tuple[int, int]:
        return (int(self.tx), int(self.ty))


def load_registration(path: str | Path) -> dict[str, Registration]:
    """读配准表；文件不存在返回空表（零行为变化）。"""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    table: dict[str, Registration] = {}
    for key, raw in (payload.get("assets") or payload).items():
        if not isinstance(raw, dict):
            continue
        table[key] = Registration(
            scale=float(raw.get("scale", 1.0)),
            tx=int(raw.get("tx", 0)),
            ty=int(raw.get("ty", 0)),
        )
    return table


def save_registration(path: str | Path, table: dict[str, Registration], *, base: str = "") -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"base": base, "assets": {
        key: {"scale": reg.scale, "tx": reg.tx, "ty": reg.ty} for key, reg in sorted(table.items())
    }}
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return file_path


def apply_registration(
    image: Image.Image,
    registration: Registration,
    canvas_size: tuple[int, int] | None = None,
    *,
    anchor: tuple[int, int] = (0, 0),
) -> Image.Image:
    """按配准参数缩放 + 平移，返回与 ``canvas_size`` 同尺寸的 RGBA。"""
    canvas_size = canvas_size or image.size
    width, height = image.size
    target = registration.apply(width, height)
    scaled = image.convert("RGBA").resize(target, Image.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = int(anchor[0] + registration.tx)
    y = int(anchor[1] + registration.ty)
    canvas.alpha_composite(scaled, (x, y))
    return canvas


def derive_size(
    source_size: tuple[int, int],
    *,
    height: int | None = None,
    width: int | None = None,
) -> tuple[int, int]:
    """按原图实际比例派生尺寸（旧实现写死 2:3，会拉伸剪影）。"""
    src_w, src_h = source_size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source_size 必须为正")
    if height is None and width is None:
        return (src_w, src_h)
    if height is not None:
        return (max(1, int(round(height * src_w / src_h))), int(height))
    assert width is not None
    return (int(width), max(1, int(round(width * src_h / src_w))))


def registration_error(
    base_registration: Registration,
    other: Registration,
) -> dict[str, float]:
    """两张素材配准后的偏差（供"偏差 ≤1px"的验收脚本使用）。"""
    bw, bh = base_registration.apply(100, 100)
    ow, oh = other.apply(100, 100)
    return {
        "scale_delta": abs(other.scale - base_registration.scale),
        "tx_delta": float(abs(other.tx - base_registration.tx)),
        "ty_delta": float(abs(other.ty - base_registration.ty)),
        "size_delta": float(abs(bw - ow) + abs(bh - oh)),
    }


def table_from_pairs(pairs: Iterable[tuple[str, float, int, int]]) -> dict[str, Registration]:
    return {name: Registration(scale=s, tx=x, ty=y) for name, s, x, y in pairs}


__all__ = [
    "Registration",
    "apply_registration",
    "derive_size",
    "load_registration",
    "registration_error",
    "save_registration",
    "table_from_pairs",
]
