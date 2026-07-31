"""冒烟：简报→规划书→proceed 管线，验证 brief/plan 流转到 structure 阶段。"""
import json
import re
import time
import urllib.request

content = open(r"D:\clipweight client\文理\content.md", encoding="utf-8").read()
script = re.split(r"\*\*文案[：:]\*\*", content)[1].strip()[:3000]


def post(path: str, body: dict, timeout: int):
    req = urllib.request.Request(
        "http://localhost:8000" + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("[1] init")
r = post("/api/requirements/init", {
    "topic": "文理之争", "script_text": script,
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "audio_duration_sec": 300,
}, 30)
sid = r["session_id"]
print("    session:", sid)

print("[2] chat 简报")
r = post("/api/requirements/chat", {"session_id": sid, "message": f"我的选题是：文理之争。文稿：{script}。预估时长：300秒。请帮我生成创意简报。"}, 300)
print("    brief:", bool(r.get("creative_brief")))

print("[3] chat 确认 → 规划书")
r = post("/api/requirements/chat", {"session_id": sid, "message": "确认，请生成完整的制作规划书。"}, 300)
print("    plan:", bool(r.get("production_plan")))

print("[4] proceed → 启动管线")
r = post("/api/requirements/proceed", {"session_id": sid, "persona_id": "zam_knowledge_critical", "category_plugin_id": "knowledge_longform"}, 30)
print("    pipeline_id:", r.get("pipeline_id"))
print("    观察后端日志确认 structure 阶段 brief/plan 消费...")
