"""领域事件词表（R3）——把"用户在问什么"变成可检索的事件。

**为什么是词表而不是模型**：用户真正会说的话是有限且领域化的（"进球""开箱""降价""踩坑"）。
一份可维护的词表 + 时间对齐，能回答绝大多数"找 XX 那一段"的请求，成本接近零、可解释、
可审计（每条事件都能给出命中的**原句**作为证据）。专用模型（如足球动作识别）是后续精度
上限的选择，不是起点。

词表组织：`{domain: {event_type: [词...]}}`。`generic` 覆盖通用剪辑关注点
（介绍/结论/对比/价格/步骤/注意），其余按题材扩展；可用设置项 JSON 覆盖或追加。
"""

from __future__ import annotations

from typing import Any

#: 内置领域词表
DOMAIN_LEXICONS: dict[str, dict[str, list[str]]] = {
    "generic": {
        "intro": ["大家好", "欢迎来到", "欢迎收看", "本期", "开篇", "先说一下", "今天我们来", "今天聊聊"],
        "conclusion": ["总结", "总之", "最后", "综上", "结论", "今天就到这里", "划重点"],
        "comparison": ["对比", "相比", "区别", "差异", "不如", "更好", "更差"],
        "price": ["价格", "多少钱", "成本", "预算", "性价比", "便宜", "贵"],
        "steps": ["第一步", "第二步", "接下来", "步骤", "流程"],
        "caution": ["注意", "小心", "风险", "坑", "不建议", "避免", "翻车"],
        "question": ["为什么", "怎么做", "如何", "是什么", "能不能"],
    },
    "sports": {
        "goal": ["进球", "破门", "射门", "打门", "入网", "得分", "绝杀", "梅开二度",
                 "帽子戏法", "球进了", "进啦", "建功", "扳平", "反超"],
        "near_miss": ["击中门柱", "偏出", "越位", "被扑出", "擦柱而出", "打偏"],
        "penalty": ["点球", "任意球", "角球", "罚球"],
        "card": ["黄牌", "红牌", "犯规", "警告", "判罚"],
        "save": ["扑救", "神扑", "门将", "扑出"],
        "substitution": ["换人", "替补", "调整阵容"],
        "celebration": ["庆祝", "欢呼", "球迷", "锁定胜局"],
    },
    "food": {
        "unbox": ["开箱", "拆袋", "打开包装"],
        "taste": ["试吃", "尝一下", "口感", "味道", "好吃", "难吃"],
        "cook": ["下锅", "翻炒", "加热", "出锅", "配方", "食材"],
        "review": ["推荐", "回购", "踩雷", "性价比"],
    },
    "tech_review": {
        "spec": ["参数", "配置", "跑分", "芯片", "内存", "电池"],
        "benchmark": ["实测", "帧率", "散热", "续航", "充电"],
        "verdict": ["值得买", "不推荐", "缺点", "优点", "总结"],
        "unbox": ["开箱", "外观", "手感"],
    },
    "knowledge": {
        "definition": ["定义", "概念", "指的是", "所谓"],
        "cause": ["原因", "为什么", "导致", "因为"],
        "example": ["例如", "举例", "比如", "举个例子"],
        "summary": ["小结", "回顾", "总结", "划重点"],
    },
}

#: 事件类型的中文名（给下游展示用）
EVENT_LABELS: dict[str, str] = {
    "intro": "开场", "conclusion": "结论", "comparison": "对比", "price": "价格",
    "steps": "步骤", "caution": "注意/风险", "question": "提问",
    "goal": "进球", "near_miss": "错失良机", "penalty": "定位球/点球", "card": "判罚",
    "save": "扑救", "substitution": "换人", "celebration": "庆祝",
    "unbox": "开箱", "taste": "试吃", "cook": "烹饪", "review": "评价",
    "spec": "参数", "benchmark": "实测", "verdict": "结论", "definition": "定义",
    "cause": "原因", "example": "举例", "summary": "小结",
}

VALID_DOMAINS: tuple[str, ...] = tuple(DOMAIN_LEXICONS)


def resolve_lexicon(domain: str = "generic", extra_json: str = "") -> dict[str, list[str]]:
    """取词表：`generic` 始终并入（通用关注点在每个题材里都成立）。

    `extra_json` 形如 `{"sports": ["远射", "倒钩"]}`（同类型事件则追加词，新类型则新增）。
    """
    key = (domain or "generic").strip().lower()
    lex: dict[str, list[str]] = {k: list(v) for k, v in DOMAIN_LEXICONS["generic"].items()}
    if key not in DOMAIN_LEXICONS:
        return lex
    for ev, words in DOMAIN_LEXICONS[key].items():
        bucket = lex.setdefault(ev, [])
        for w in words:
            if w not in bucket:
                bucket.append(w)
    if extra_json and extra_json.strip():
        try:
            import json

            data = json.loads(extra_json)
            if isinstance(data, dict):
                for ev, words in data.items():
                    bucket = lex.setdefault(str(ev), [])
                    for w in (words if isinstance(words, (list, tuple)) else [words]):
                        w = str(w).strip()
                        if w and w not in bucket:
                            bucket.append(w)
        except Exception:
            pass  # 非法 JSON 忽略：词表是增强，不该让索引失败
    return lex


def spot_events_in_text(
    text: str,
    *,
    lexicon: dict[str, list[str]] | None = None,
    t0: float = 0.0,
    t1: float | None = None,
    source: str = "text",
) -> list[dict[str, Any]]:
    """在一段文本里找事件，返回 `[{type, label, evidence, t0, t1, confidence}]`。

    **时间戳是"这段话的时间范围"**，不是事件发生的精确时刻——因为事件是从文本推出来的，
    文本片段的时间精度就是事件的时间精度（这一点在 `confidence` 与文档里都写清楚）。
    """
    text = (text or "").strip()
    if not text:
        return []
    lex = lexicon or resolve_lexicon("generic")
    out: list[dict[str, Any]] = []
    for ev_type, words in lex.items():
        hits = [w for w in (words or []) if w and w in text]
        if not hits:
            continue
        # 证据句：命中词附近的一小段原文（让"为什么判成进球"可核查）
        first = hits[0]
        idx = text.find(first)
        evidence = text[max(0, idx - 12): idx + len(first) + 12].strip()
        out.append({
            "type": ev_type,
            "label": EVENT_LABELS.get(ev_type, ev_type),
            "t0": round(float(t0), 3),
            "t1": round(float(t1 if t1 is not None else t0), 3),
            "matched": hits[:6],
            "evidence": evidence,
            "source": source,
            # 命中词越多越可信；单一弱命中给低置信度（下游可据此过滤）
            "confidence": round(min(0.9, 0.45 + 0.15 * len(hits)), 2),
        })
    out.sort(key=lambda ev: (-ev["confidence"], ev["t0"]))
    return out
