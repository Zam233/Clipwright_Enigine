"""定点测试：跳过 structure/material/edit/audio/quality，只跑 animation 的 3 个片段。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from clipwright.config import settings
from clipwright.services import settings_store
settings_store.apply_all(settings)
from clipwright.plugins import PluginLoader

_loader = PluginLoader(plugin_dir=Path(settings.plugin_dir), data_dir=Path(settings.plugin_data_dir))
_loader.load_all()

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.schema.agent import AgentContext, AnimationInput
from clipwright.schema.timeline import Timeline

ROOT = Path("workspaces/proj_f4727a36c443-36c443")
EDIT_ART = ROOT / "artifacts" / "art_22ddd547.json"
TARGETS = ["c_5b814ede", "c_123fa956", "c_c192ba06"]  # 箭头/对比/文字动画


async def main() -> None:
    data = json.loads(EDIT_ART.read_text(encoding="utf-8"))
    timeline = Timeline.model_validate(data)
    ctx = AgentContext(
        pipeline_id="targeted_animation_test",
        persona_id="zam_knowledge_critical",
        category_plugin_id="knowledge_longform",
        topic="从 PCL 到 HMCL：我们的 Minecraft 自由社区正在失去什么？",
        extra_params={
            "workspace_root": str(ROOT.resolve()),
            "workspace_project_id": "proj_f4727a36c443",
        },
    )
    inp = AnimationInput(context=ctx, timeline=timeline, retry_clip_ids=TARGETS)
    out = await AnimationAgent().execute(inp, ctx)
    anim = [c for t in (out.timeline.tracks if out.timeline else [])
            for c in (t.clips or []) if str(t.kind) == "animation"]
    print("decision=", out.decision)
    print("animation_clips=", len(anim))
    for c in anim:
        m = c.metadata or {}
        print(" clip", c.id, m.get("anim_type"), m.get("anim_name"),
              "html", len(str(m.get("mg_html") or "")),
              "params", sorted((m.get("mg_params") or {}).keys())[:5])
    print("failed=", json.dumps(out.issues, ensure_ascii=False))
    print("degradations=", [d.kind for d in out.degradations])
    if out.timeline is not None:
        dst = ROOT / "reports" / "targeted-animation-test.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out.timeline.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
        print("timeline_written=", dst)


asyncio.run(main())
