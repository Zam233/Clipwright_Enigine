"""轮65 补丁1：persona 字段接线（loudnorm/min_duration/base_shot_ms）。执行后删除。"""
from pathlib import Path
import re

# ── 1a. 四个 category 插件：base_shot_duration_ms 显式值优先于密度档位 ──
category_files = [
    "clipwright/category/knowledge_longform.py",
    "clipwright/category/vlog_daily.py",
    "clipwright/category/kichiku_fastcut.py",
    "clipwright/category/digital_review.py",
]
for cf in category_files:
    p = Path(cf)
    src = p.read_text(encoding="utf-8")
    old = '        shot_params = density_map.get(rhythm.cut_density_tier, density_map["medium"])'
    new = '''        shot_params = density_map.get(rhythm.cut_density_tier, density_map["medium"])
        # 批8：Persona 显式 base_shot_duration_ms 优先于密度档位推导
        if getattr(rhythm, "base_shot_duration_ms", None):
            shot_params = {**shot_params,
                           "base_shot_ms": int(rhythm.base_shot_duration_ms)}'''
    assert src.count(old) == 1, f"{cf} anchor={src.count(old)}"
    src = src.replace(old, new)
    p.write_text(src, encoding="utf-8")
    print("base_shot override:", cf)

# ── 1b. quality：min_duration_sec 接线 ──
p = Path("clipwright/agents/quality_agent.py")
src = p.read_text(encoding="utf-8")
i = src.find("# ── 1. 时长校验 ──")
seg = src[i:i+500]
print("duration check:", repr(seg[:380]))
PYEOF
