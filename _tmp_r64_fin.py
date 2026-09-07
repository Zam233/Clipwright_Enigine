"""轮64 收尾：看板勾选 + fix-plan 轮64记录。执行后删除。"""
from pathlib import Path

# ── 看板：勾选 6.4/6.5/7.2/7.4 + 轮64段 ──
p = Path("docs/remediation-master-plan.md")
src = p.read_text(encoding="utf-8")
reps = [
    ("| 6.4 | EDL/FCPXML 互操作修复 | services/edl.py |⏸ 延后 |",
     "| 6.4 | EDL/FCPXML 互操作修复（轮64完成：编号连续/reel清洗/转场码映射/XML转义/file URI/ntsc/字幕轨/导入保留位置/DOCTYPE守卫收窄） | services/edl.py |✅ |"),
    ("| 6.5 | 归档 zip：含成片/重名消歧/重映射表/流式 + 导入端点 | api/project.py:242-286 |⏸ 延后 |",
     "| 6.5 | 归档 zip：重名消歧/重映射表/成片打包 + 导入端点（轮64完成；流式打包延后） | api/project.py:242-286 |✅ |"),
    ("| 7.2 | 假成功清理：transcribe 已删、SemanticMatch 澄清（TrackingText/VisionLLM 改造延后） |",
     "| 7.2 | 假成功清理：transcribe 已删、SemanticMatch 澄清、TrackingText 改 DEPENDENCY_MISSING（VisionLLM 回退带 fallback 标记保留） |"),
    ("| 7.4 | Hook 治理：unload/reload 清理（两插件补 plugin_id、text_animations 归属延后） |",
     "| 7.4 | Hook 治理：unload/reload 清理 + logic_animations/diagram_style 补 plugin_id（text_animations 注册归属延后） |"),
]
for old, new in reps:
    assert src.count(old) == 1, old[:40]
    src = src.replace(old, new)

# 轮64段追加在轮63段之后（批次8标题前）
anchor = "## 批次 8（后续独立任务，不在本轮）"
addition = """## 轮 64：遗留待办收尾（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 6.4 | EDL/FCPXML 全量修复（事件编号/reel 清洗/FROM CLIP NAME/转场码映射/XML 转义/file URI/ntsc 修正/in-out 点/字幕轨导出/导入保留时间位置/DOCTYPE 守卫收窄为 ENTITY） | ✅ |
| 6.5 | 归档导出重构（重名消歧/archive_media_map 重映射表/时间线 asset_id 归档化/成片打包）+ POST /import-archive 导入还原端点 | ✅ |
| 批8a | DELETE /api/render/artifacts 过期产物清理端点（管理员/jwt 管控） | ✅ |
| D3 | semantic QA 简报回退链（script_skeleton.brief） | ✅ |

回归：后端 1465 passed / 0 失败（含归档往返新增 3 项）。

""" + anchor
assert src.count(anchor) == 1
src = src.replace(anchor, addition)
p.write_text(src, encoding="utf-8")
print("board round64 added")

# ── fix-plan §10 轮 64 ──
p = Path("docs/fix-and-feature-plan.md")
src = p.read_text(encoding="utf-8")
anchor = "- ✅ 回归：后端 1462 passed / 0 失败（移除 1 个废弃端点用例）· 前端 tsc 0 错误 + vitest 379/379"
addition = anchor + """

### 执行轮次 64（遗留待办收尾：EDL/FCPXML、归档往返、产物清理）
- ✅ 6.4 EDL/FCPXML 互操作修复：EDL 事件编号连续（跳过类型不再占号）+ reel 用清洗资产标识（旧实现取路径前 8 字符，NLE 无法 relink）+ FROM CLIP NAME 可读名 + 转场名映射合法 EDL 码；FCPXML name/pathurl XML 转义 + pathurl 规范 file URI（旧 file://J:\\... 非法）+ ntsc 仅 29.97 + clipitem id 唯一 + 字幕/文字轨导出（文本入 comments，此前整轨静默丢失）+ 导入保留 start/end 时间位置（旧实现恒 0）+ DOCTYPE 守卫收窄为仅拒内部实体（标准 DOCTYPE 声明不再误拒）
- ✅ 6.5 归档 zip 重构：时间线媒体重名消歧（clip_2/clip_3 递增）；archive_media_map 重映射表写入 project.json 并将时间线 asset_id 替换为归档相对路径（旧实现保留绝对路径，归档自不可恢复）；agent_state.output_path 指向的成片一并打包；新增 POST /api/project/import-archive 导入还原（zip slip 防护/500MB 上限/媒体解包 PluginData/archives/<id>/ 并重映射回本地路径）
- ✅ 批8a：新增 DELETE /api/render/artifacts 过期渲染产物清理端点（jwt 管理员管控，off 模式放行；audit 记录）
- ✅ 批7 收尾：TrackingTextTool 假 SUCCESS → DEPENDENCY_MISSING；logic_animations/diagram_style Hook 注册补 plugin_id（disable 后钩子不再残留执行）；unload/reload 补 HookRegistry 注销
- ✅ 批3.6/D3 收尾：edit scene_time 死变量清理；semantic QA 简报回退链（creative_brief 缺失时读 script_skeleton.brief）
- ✅ 回归：后端 1465 passed / 0 失败（含归档往返新增 3 项）"""
assert src.count(anchor) == 1
src = src.replace(anchor, addition)
p.write_text(src, encoding="utf-8")
print("fix-plan round64 appended")
