"""B3-T6 删除后的验收断言（裁定 3：没有插件就没有 TTS 能力）。

为什么要单独一个脚本：删除之后「配音能力消失」是**预期行为**，pytest 里那些假设
"内置总会补位"的用例已经随之改写（见 tests/clipwright/test_tts_provider_registry.py
与 test_tts_feature_dub.py）。这里把**行为边界**固化成一条命令，不依赖 pytest，
也不写任何落盘数据：

    装插件 → 三个 provider + dub_script 都在，/api/voice/dub 不是 503
    摘插件 → provider 列表为空、resolve 为 None、技能为 None、/api/voice/dub → 503
    切句   → 行为与搬运前一致（3 个固定用例 + paragraph 模式）

用法::

    py -3.14 scripts/verify_tts_teardown.py
    py -3.14 scripts/verify_tts_teardown.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TTS_PLUGINS = ("tts_feature", "tts_cosyvoice", "tts_qwen", "tts_minimax")


def snapshot(tag: str) -> dict:
    from clipwright.providers.tts import resolve_tts_entry, tts_provider_names
    from clipwright.skill.registry import SkillRegistry
    from clipwright.tool.capabilities import skill_resolve

    skill = SkillRegistry.get("dub_script")
    return {
        "tag": tag,
        "tts_ids": sorted(tts_provider_names()),
        "skill_dub_script": skill is not None,
        "skill_owner": SkillRegistry.owner("dub_script") if skill else "",
        "resolve_dub": getattr(skill_resolve("dub"), "name", None),
        "resolve_tts": resolve_tts_entry("cosyvoice") is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B3-T6 删除后的行为边界验收")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient

    from clipwright.main import app
    from clipwright.plugins.contribution import ContributionRegistry

    out: list[dict] = []
    with TestClient(app, raise_server_exceptions=False) as c:
        # 1) 插件在：能力齐全
        out.append(snapshot("插件全在"))
        assert out[-1]["tts_ids"] == ["cosyvoice", "minimax", "qwen-tts"], out[-1]
        assert out[-1]["skill_dub_script"] is True, "dub_script 应由 tts_feature 提供"
        assert out[-1]["skill_owner"] == "tts_feature", out[-1]
        assert out[-1]["resolve_dub"] == "dub_script", out[-1]
        ok = c.post("/api/voice/dub", json={"voice_id": "x", "text": "第一句。第二句。"})
        assert ok.status_code != 503, f"插件在时不应 503，实际 {ok.status_code}"

        # 2) 摘掉插件：能力整体消失，且不补位
        for pid in TTS_PLUGINS:
            ContributionRegistry.unregister_plugin(pid)
        out.append(snapshot("摘掉插件"))
        assert out[-1]["tts_ids"] == [], f"删除内置注册后应无 TTS provider：{out[-1]['tts_ids']}"
        assert out[-1]["resolve_tts"] is False, "resolve_tts_entry 不应再懒注册内置"
        assert out[-1]["skill_dub_script"] is False, "dub_script 不应回落到内置（内置已删）"
        assert out[-1]["resolve_dub"] is None, "能力名 dub 应解析不到"
        dub = c.post("/api/voice/dub", json={"voice_id": "x", "text": "第一句。"})
        out.append({
            "tag": "插件摘掉后的 /api/voice/dub",
            "status": dub.status_code,
            "detail": str((dub.json() or {}).get("detail"))[:80],
        })
        assert dub.status_code == 503, f"应 503，实际 {dub.status_code}"

        # 3) 切句策略在插件里且行为不变
        from plugins.tts_feature.split_text import split_text

        cases = {
            "第一句话。第二句话。第三句话。": ["第一句话。", "第二句话。", "第三句话。"],
            "你好！世界！": ["你好！", "世界！"],
            "第一行\n第二行。": ["第一行第二行。"],
        }
        for src, want in cases.items():
            got = split_text(src)
            assert got == want, f"切句结果变了：{src!r} → {got!r}（期望 {want!r}）"
        assert split_text("段落一。\n\n段落二。", mode="paragraph") == ["段落一。", "段落二。"]
        out.append({"tag": "插件 split_text 行为", "ok": True})

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for item in out:
            print(json.dumps(item, ensure_ascii=False))
        print("\n全部通过：删除后「没有插件就没有 TTS 能力」，且切句行为不变")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
