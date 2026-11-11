"""审计 PRD 2026-11-09 第六轮定向回归（BE-08 / PL-12 缓存键）。

- BE-08：duplicate 的 owner 补写失败必须回滚删除副本并返回 500，
  不返回无主副本（`except Exception: pass` 已移除）。
- PL-12：usePluginSlots 读写缓存键一致（不再每次挂载空跑请求）。
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path


def test_duplicate_owner_write_failure_no_longer_silent():
    """源码级断言：owner 写失败分支不得静默 pass，必须回滚并 500。"""
    from clipwright.api import project as pj

    src = inspect.getsource(pj.duplicate_project)
    assert "副本归属写入失败，已回滚" in src
    # 旧的静默吞错形态必须消失：save 后紧跟裸 `except Exception:\n pass`
    assert "except Exception:\n                pass" not in src
    assert "_manager.delete(dup_id)" in src


def test_duplicate_uses_current_user_id():
    """owner 取自 current_user_id（账号剥离后恒 None → 不写归属、无无主风险）。"""
    from clipwright.api import project as pj

    src = inspect.getsource(pj.duplicate_project)
    assert "owner_id = current_user_id(request)" in src


# ── PL-12：usePluginSlots 缓存键一致 ─────────────────────────────────────

def test_use_plugin_slots_reads_same_cache_key_it_writes():
    """useState 初始化必须读 `slot:${slot}`（与 fetchSlotContributions 写入键一致），
    否则缓存永不命中、每次挂载都多发请求。"""
    from pathlib import Path

    test_dir = Path(__file__).parent          # .../tests/clipwright
    client_root = test_dir.parents[1]         # .../tests -> J:\Clipweight-Client? no: tests 的父是仓库根
    # tests/clipwright -> tests -> J:\Clipwright；前端仓库在同盘的 J:\Clipweight-Client
    client_root = Path(r"J:\Clipweight-Client")
    check = (
        "const fs=require('fs');"
        "const s=fs.readFileSync('src/features/plugins/usePluginSlots.ts','utf8');"
        "const init='cache.get(slot)?.items';"
        "if(s.includes(init)){console.error('BARE_KEY');process.exit(1);}"
        "if(!s.includes('cache.get(cacheKey)?.items')){console.error('MISSING_KEY');process.exit(1);}"
        "console.log('OK');"
    )
    r = subprocess.run(["node", "-e", check], cwd=str(client_root), capture_output=True, text=True)
    assert r.returncode == 0, f"cache key mismatch: {r2_out if False else r.stdout} {r.stderr}"
