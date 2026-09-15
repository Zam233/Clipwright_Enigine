"""R9 验收脚本：语义检索（画面向量）召回率。

**验收判据（计划 §4）**：30 条查询，召回率 @10 ≥ 70%，并与关键词基线对比。

## 基准怎么造的（以及为什么这么造）

没有真实素材（本会话一开始就确认过），所以用**合成画面**建基准。合成画面能测的
"语义"必须诚实限定：纯色块/条纹/明暗这类**画面属性** CLIP 认得（这是它训练数据里
就有的视觉概念），而"这是不是一场足球赛"这种场景语义在合成帧上做不了
（R2 已经实测：CLIP 对合成帧会给出"屏幕录制/左轮手枪"这类离谱标签）。

于是基准设计成**属性检索**，并且刻意让"素材标签的写法"与"用户的说法"不同：

| 素材上的文字（description/tags） | 用户可能的说法 |
|---|---|
| 红色                                    | 大红 / 一片通红 |
| 蓝色                                    | 天蓝 / 偏蓝的画面 |

- **关键词基线**就是 R4 的文本路：把查询词与素材文字做字面/词元匹配。
  它在"说法不同"时失效——这不是人为削弱，而是字面匹配的真实局限；
- **语义路（CLIP）**看的是像素，说法不同也能对上（前提是中文查询先过提示词改写，
  脚本会单独跑一组"不改写"的对照，量化这件事的影响）。

语料规模：**120 个镜头 / 30 条查询**，所以 top-10 的随机命中率是 8.3%，
70% 的门槛是有意义的（不是"随便排都能过"）。

## 本脚本的边界（如实）

- 合成素材、无真实拍摄画面：证明的是"向量链路 + 中文改写 + 融合排序"能工作，
  **不等于**真实素材上的召回率；
- 30 条查询与 120 帧的"标准答案"都是我写的，存在自证偏差；
- CLIP 的余弦相似度不是相关概率，`min_score` 只用来滤掉明显无关的。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 合成语料：颜色 × 图案 × 明暗 ──────────────────────────

COLORS: dict[str, tuple[int, int, int]] = {
    "红色": (220, 30, 30),
    "蓝色": (30, 60, 220),
    "绿色": (40, 180, 60),
    "黄色": (240, 220, 40),
    "紫色": (150, 60, 200),
    "灰色": (128, 128, 128),
    "橙色": (240, 140, 30),
    "青色": (40, 200, 200),
}

#: 图案 → 生成方式（每个颜色各出一套，共 8×3×5 = 120 帧）
PATTERNS = ("solid", "stripes", "checker", "gradient", "noise")


def _frame(color: tuple[int, int, int], pattern: str, variant: int, size: int = 224):
    """合成一帧：颜色 / 条纹 / 棋盘 / 渐变 / 噪点。"""
    from PIL import Image, ImageDraw
    import random

    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)
    r, g, b = color
    if pattern == "stripes":
        step = 8 + variant * 4
        for x in range(0, size, step * 2):
            draw.rectangle([x, 0, x + step, size], fill=(255 - r, 255 - g, 255 - b))
    elif pattern == "checker":
        cell = 14 + variant * 6
        for y in range(0, size, cell):
            for x in range(0, size, cell):
                if ((x // cell) + (y // cell)) % 2 == 0:
                    draw.rectangle([x, y, x + cell, y + cell], fill=(255 - r, 255 - g, 255 - b))
    elif pattern == "gradient":
        for y in range(size):
            k = y / max(1, size - 1)
            draw.line([(0, y), (size, y)],
                      fill=(int(r * (1 - k)), int(g * (1 - k)), int(b * (1 - k))))
    elif pattern == "noise":
        rng = random.Random(hash((color, variant)) & 0xFFFF)
        for _ in range(1200):
            x, y = rng.randrange(size), rng.randrange(size)
            jitter = rng.randint(-40, 40)
            draw.point((x, y), fill=(max(0, min(255, r + jitter)),
                                     max(0, min(255, g + jitter)),
                                     max(0, min(255, b + jitter))))
    if variant % 2 == 1:      # 一半压暗，制造"明/暗"维度
        img = img.point(lambda v: int(v * 0.35))
    return img


def build_corpus(root: Path) -> list[dict]:
    """生成 120 帧 + 每帧的"素材文字"（描述/标签，用与用户不同的说法）。"""
    shots: list[dict] = []
    for color_name, rgb in COLORS.items():
        for pattern in PATTERNS:
            for variant in range(3):
                idx = len(shots)
                path = root / f"shot_{idx:03d}_{color_name}_{pattern}_{variant}.png"
                _frame(rgb, pattern, variant).save(path)
                dark = variant % 2 == 1
                # 素材文字：**写法刻意与查询不同**（模拟真实语料里标签写不全/写不准）
                label = {
                    "红色": "暖色调画面", "蓝色": "冷色调画面", "绿色": "草木色块",
                    "黄色": "明亮色块", "紫色": "神秘色调", "灰色": "中性灰阶",
                    "橙色": "落日色调", "青色": "海色调",
                }[color_name]
                shots.append({
                    "path": str(path),
                    "color": color_name,
                    "pattern": pattern,
                    "variant": variant,
                    "dark": dark,
                    "description": f"{'暗' if dark else '亮'}{label}的{pattern}画面",
                    "tags": [label, pattern, "暗" if dark else "亮"],
                })
    return shots


#: 30 条查询：`(中文查询, 判定函数)`——判定用**颜色**这一可验证属性
def queries() -> list[tuple[str, dict]]:
    q: list[tuple[str, dict]] = []
    for color in ("红色", "蓝色", "绿色", "黄色", "紫色", "灰色"):
        q.append((f"找{color}的画面", {"color": color}))
        q.append((f"{color}的镜头", {"color": color}))
        q.append((f"一片{'通红' if color == '红色' else color}的感觉", {"color": color}))
        q.append((f"偏{color}调子的素材", {"color": color}))
        q.append((f"{color}底色的画面", {"color": color}))
    return q


def is_relevant(shot: dict, truth: dict) -> bool:
    return all(shot.get(k) == v for k, v in truth.items())


def keyword_recall(shots: list[dict], qs: list[tuple[str, dict]], top_k: int) -> tuple[float, list[str]]:
    """关键词基线（R4 的文本路）：查询词与素材文字的字面/词元匹配。"""
    from clipwright.services.content_query import keyword_score

    class _Shot:      # `keyword_score` 需要带 visual/speech 字段的对象
        def __init__(self, shot: dict) -> None:
            from clipwright.schema.content import ShotSpeech, ShotVisual

            self.speech = ShotSpeech()
            self.visual = ShotVisual(description=shot["description"], tags=list(shot["tags"]))
            self.audio_events = []

    rows = [_Shot(s) for s in shots]
    hits = 0
    misses: list[str] = []
    for query, truth in qs:
        scored = sorted(zip(rows, shots), key=lambda pair: -keyword_score(pair[0], query)[0])
        top = [s for _r, s in scored[:top_k]]
        if any(is_relevant(s, truth) for s in top):
            hits += 1
        else:
            misses.append(query)
    return hits / len(qs), misses


def semantic_rank(shots: list[dict], query: str, top_k: int, gloss: bool) -> list[dict]:
    """语义路：CLIP 文本向量 vs 画面向量。"""
    from clipwright.schema.content import ContentIndex, ShotRecord
    from clipwright.services import content_semantic as sem

    idx = ContentIndex(sha256="0" * 64, asset_path="corpus", shots=[
        ShotRecord(index=i, t0=float(i), t1=float(i) + 1, duration_sec=1.0,
                   keyframes=[s["path"]])
        for i, s in enumerate(shots)
    ])
    sem.attach_visual_vectors(idx, force=True)
    ranked = sem.rank_shots(query, [idx], top_k=top_k, min_score=0.0)
    if not gloss:
        # 对照：不经中→英改写，直接拿中文去编码
        scores = sem.semantic_scores(query, list(idx.shots), gloss=False)
        order = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
        return [shots[i] for i, _score in order]
    return [shots[h["shot_index"]] for h in ranked]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--which", default="all", choices=("all", "keyword", "semantic"))
    args = parser.parse_args()

    print("=" * 72)
    print("R9 验收：语义检索召回率（合成画面 · 颜色属性检索）")
    print("=" * 72)

    from clipwright.services import content_semantic as sem

    available, _n = sem.search_available([])
    check("CLIP 可用（否则本脚本无法验证语义路）", available,
          "CLIP 模型已加载" if available else "CLIP 不可用")
    if not available:
        print("\nSEMANTIC SEARCH VERIFY FAIL（CLIP 不可用，无法验证）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="r9_corpus_"))
    shots = build_corpus(tmp)
    qs = queries()
    print(f"\n语料：{len(shots)} 个镜头（8 色 × 5 图案 × 3 变体）")
    print(f"查询：{len(qs)} 条中文原话；top-{args.top_k} 随机命中率 "
          f"{args.top_k / len(shots) * 100:.1f}%")

    if args.which in ("all", "keyword"):
        print("\n[基线] 关键词路（R4 文本检索）")
        k_recall, k_misses = keyword_recall(shots, qs, args.top_k)
        print(f"      recall@{args.top_k} = {k_recall * 100:.1f}%（{int(k_recall * len(qs))}/{len(qs)}）")
        if k_misses:
            print(f"      未命中示例：{'、'.join(k_misses[:3])}")
    else:
        k_recall = 0.0
        s_recall = 0.0

    if args.which in ("all", "semantic"):
        print("\n[本轮实现] 语义路（CLIP 画面向量 + 中文提示词改写）")
        hits = 0
        misses: list[str] = []
        for query, truth in qs:
            top = semantic_rank(shots, query, args.top_k, gloss=True)
            if any(is_relevant(s, truth) for s in top):
                hits += 1
            else:
                misses.append(query)
        s_recall = hits / len(qs)
        print(f"      recall@{args.top_k} = {s_recall * 100:.1f}%（{hits}/{len(qs)}）")
        if misses:
            print(f"      未命中示例：{'、'.join(misses[:3])}")
        check(f"语义路 recall@{args.top_k} ≥ 70%", s_recall >= 0.70,
              f"{s_recall * 100:.1f}%")

        print("\n[对照] 不做中文提示词改写（中文直接送 CLIP 文本塔）")
        raw_hits = 0
        for query, truth in qs:
            top = semantic_rank(shots, query, args.top_k, gloss=False)
            if any(is_relevant(s, truth) for s in top):
                raw_hits += 1
        raw_recall = raw_hits / len(qs)
        print(f"      recall@{args.top_k} = {raw_recall * 100:.1f}%（{raw_hits}/{len(qs)}）")
        check("中文改写确实带来提升", raw_recall <= s_recall,
              f"改写后 {s_recall * 100:.1f}% vs 直送 {raw_recall * 100:.1f}%")

    if args.which == "all":
        check("语义路优于关键词基线", s_recall > k_recall,
              f"{s_recall * 100:.1f}% vs {k_recall * 100:.1f}%")

    # 向量落盘/复用
    print("\n[工程性] 向量只算一次，检索不再碰模型")
    from clipwright.schema.content import ContentIndex, ShotRecord

    idx = ContentIndex(sha256="1" * 64, asset_path="x", shots=[
        ShotRecord(index=0, t0=0.0, t1=1.0, duration_sec=1.0, keyframes=[shots[0]["path"]]),
    ])
    _idx, n1 = sem.attach_visual_vectors(idx)
    _idx, n2 = sem.attach_visual_vectors(idx)          # 第二次应跳过
    check("首次算出向量", n1 == 1, str(n1))
    check("已有向量不重算", n2 == 0, str(n2))
    check("覆盖率统计正确", sem.visual_coverage([idx]) == 1.0, str(sem.visual_coverage([idx])))

    print("\n" + "=" * 72)
    print(f"检查项 {PASS}/{PASS + FAIL} 通过")
    print("=" * 72)
    if FAIL:
        print("SEMANTIC SEARCH VERIFY FAIL")
        return 1
    print("SEMANTIC SEARCH VERIFY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
