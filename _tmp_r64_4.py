"""批7.4/7.2/3.6/D3 收尾补丁（修正 diagram_style 锚点）。执行后删除。"""
from pathlib import Path

# ── 7.4b diagram_style：Hook 注册补 plugin_id（缩进 8 空格，initialize 内）──
p = Path("plugins/diagram_style/presets.py")
src = p.read_text(encoding="utf-8")
old = "        HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, register_style_presets)\n        from clipwright.config import logger"
new = ("        HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, register_style_presets,\n"
       "                              plugin_id=\"diagram_style\")\n"
       "        from clipwright.config import logger")
assert src.count(old) == 1, f"diagram anchor={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("diagram_style plugin_id done")

# ── 7.2 TrackingText 诚实状态 ──
p = Path("clipwright/tool/animation.py")
src = p.read_text(encoding="utf-8")
old = '''        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={"text": text, "note": "placeholder — Manim not yet integrated"},
            warning="Tracking text requires Manim — not yet integrated",
        )'''
new = '''        return ToolExecResult(
            # 批7：诚实状态——占位假 SUCCESS 会让调用方误以为已产出动画
            status=ToolStatus.DEPENDENCY_MISSING,
            tool_name=self.name,
            output={"text": text, "note": "tracking text requires Manim (not yet integrated)"},
            error="Tracking text requires Manim — not yet integrated",
        )'''
assert src.count(old) == 1, f"tracking={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("7.2 tracking done")

# ── 3.6a scene_time 死变量清理 ──
p = Path("clipwright/agents/edit_agent.py")
src = p.read_text(encoding="utf-8")
old = "        scene_time = 0.0  # 本地时间游标（替代共享 current_time，仅推进、不用于轨道放置）"
assert src.count(old) == 1
src = src.replace(old, "")
old2 = "            scene_time += seg_dur\n"
assert src.count(old2) == 1
src = src.replace(old2, "")
p.write_text(src, encoding="utf-8")
print("3.6 scene_time removed")

# ── D3 semantic QA 回退读 script_skeleton.brief ──
p = Path("clipwright/agents/quality_agent.py")
src = p.read_text(encoding="utf-8")
old = "                semantic_issues = await self._check_semantic_qa(\n                    timeline, input_data.creative_brief, context\n                )"
new = '''                # 批D(D3)：proceed 复用路径下简报摘要随 script_skeleton.brief
                # 流转——creative_brief 缺失（raw /run-async）时回退使用
                _brief_for_qa = input_data.creative_brief
                if not _brief_for_qa:
                    _sk = getattr(input_data, "script_skeleton", None) or {}
                    _sk_brief = _sk.get("brief") if isinstance(_sk, dict) else None
                    if isinstance(_sk_brief, dict) and _sk_brief:
                        _brief_for_qa = _sk_brief
                semantic_issues = await self._check_semantic_qa(
                    timeline, _brief_for_qa, context
                )'''
assert src.count(old) == 1, f"D3 anchor={src.count(old)}"
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print("D3 done")
