# PersonaForge 使用教程

PersonaForge 是内容视频编排引擎的 Persona 自动构建模块。它能通过 LLM 将你的日常语言、脚本文本或对话记录，自动编译成完整的 Persona 配置。

---

## 先决条件

```bash
# 启动引擎
cd J:/Clipwright
python -m uvicorn clipwright.main:app --reload --host 0.0.0.0 --port 8000
```

有两种运行模式：

| 模式 | 要求 | 效果 |
|------|------|------|
| **LLM 模式（推荐）** | 配置 `CLIPWRIGHT_LLM_API_KEY` | 精确映射参数，理解复杂描述 |
| **离线模式** | 无需任何 key | 关键词启发式 + 基础统计，结果较粗糙 |

> 没有 API key 也能用，所有端点都带离线回退。下文每个例子都标注了「在线/离线均可」。

---

## 场景一：一句话创建你的创作人格

> 适用：新 UP 主快速起步，15 秒生成初版 Persona

你只需要用日常语言描述你的风格，剩下的交给 LLM。

```bash
curl -X POST http://localhost:8000/api/persona/forge/from-prompt \
  -H "Content-Type: application/json" \
  -d '{
    "description": "我做数码评测，风格毒舌吐槽，说话直接不绕弯子。画面喜欢高对比度、暗色调，文字用红字高亮重点参数。节奏偏快，平均3-5秒一剪。BGM用电子工业风。",
    "persona_id": "my_tech_sarcastic"
  }'
```

**返回**（简化）：

```json
{
  "persona_id": "my_tech_sarcastic",
  "parameter": {
    "identity": {
      "tone": "tech_enthusiast",
      "knowledge_domains": ["digital_review", "tech"]
    },
    "language": {
      "academic_density": 0.05,
      "slang_ratio": 0.2,
      "max_sentence_len": 25
    },
    "rhythm": {
      "cut_profile": "rapid_fire",
      "base_shot_duration_ms": 4000,
      "cut_density_tier": "high"
    },
    "visual": {
      "palette": "high_contrast_dark"
    },
    "audio": {
      "bgm_slots": {
        "review_backing": ["electronic_industrial"]
      }
    }
  }
}
```

**验证**：生成后自动保存到 `personas/my_tech_sarcastic/`，你可以查看：

```bash
cat personas/my_tech_sarcastic/persona.yaml
```

**更多风格描述示例**：

| 描述 | 预期 tone |
|------|-----------|
| "我做知识区长视频，偏学术，喜欢引用理论，画面简洁，留白多" | `critical_intellectual` |
| "日常 Vlog，轻松温暖，像朋友聊天，画面明亮，BGM 用吉他" | `warm_storyteller` |
| "鬼畜二创，高速快剪，满屏特效，一秒一剪，节奏炸裂" | `casual_humor` |

> 在线/离线均可。离线模式下通过关键词匹配推断 tone。

---

## 场景二：从你的脚本生成 Persona

> 适用：已有历史视频脚本/字幕，希望精确提取语言风格参数

准备一段你的脚本文本（支持 `.txt` / `.srt` / `.md`）：

```bash
curl -X POST http://localhost:8000/api/persona/forge/from-script \
  -H "Content-Type: application/json" \
  -d '{
    "script": "今天我们来聊聊一个很多人都在问的问题：年轻人为什么会沉迷盲盒？\n\n首先，我们要理解一个核心概念——符号消费。这不是简单的买一个玩具，而是一种身份认同的投射。\n\n基于鲍德里亚的消费社会理论，我们可以从三个维度来分析这个现象……\n\n说到这里，你可能会问：那我怎么办？其实答案很简单，回到现实。",
    "persona_id": "my_academic_style",
    "persona_name": "学术分析型",
    "script_format": "txt"
  }'
```

**返回**（语言层高度精确）：

```json
{
  "persona_id": "my_academic_style",
  "parameter": {
    "identity": { "tone": "critical_intellectual" },
    "language": {
      "academic_density": 0.15,
      "slang_ratio": 0.02,
      "max_sentence_len": 35,
      "variance_target": 0.7,
      "forbidden_patterns": ["不是.*而是.*"]
    },
    "rhythm": {
      "cut_profile": "surge_pause",
      "base_shot_duration_ms": 7000
    }
  }
}
```

**提取的指标说明**：

| 指标 | 你的脚本值 | 含义 |
|------|-----------|------|
| `academic_density: 0.15` | 15% 学术词汇 | "基于"、"理论"、"维度"、"分析" 等词占比高 |
| `slang_ratio: 0.02` | 2% 口语化 | 几乎没有网络用语，风格正式 |
| `base_shot_duration_ms: 7000` | 7 秒/镜头 | 从论证结构推断——有"hook"和"real_world_return"段落，用 surge_pause 节奏 |

**离线模式**下，系统仍然能做基础文本统计：

```bash
# 即使没有 API key，以下字段也会被填充
language.academic_density  ← 基于学术词汇库检测
language.slang_ratio       ← 基于网络用语库检测
language.max_sentence_len  ← 直接从文本统计
```

> 在线模式会额外提取 tone、forbidden_patterns、论证结构等质化特征。离线模式只填充语言层可统计字段。

---

## 场景三：对话引导 — 像聊天一样创建 Persona

> 适用：不确定自己的风格，希望通过问答逐步明确

这是一个两阶段流程。

### 第一步：让系统生成问题

```bash
curl -X POST http://localhost:8000/api/persona/forge/dialogue/generate-questions \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "my_new_persona",
    "existing_answers": {}
  }'
```

**返回**（在线模式）：

```json
[
  {
    "question": "你希望观众看完视频后对你产生什么印象？冷峻犀利的技术专家、温暖亲切的朋友、还是严谨博学的学者？",
    "category": "identity",
    "field": "tone"
  },
  {
    "question": "你的视频节奏偏快（信息密集、多跳切）还是偏慢（留白多、镜头长）？",
    "category": "rhythm",
    "field": "cut_profile"
  }
]
```

### 第二步：回答并编译

记录你的回答，调用 build 接口：

```bash
curl -X POST http://localhost:8000/api/persona/forge/dialogue/build \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "my_new_persona",
    "persona_name": "我的新人格",
    "answers": {
      "identity": {
        "tone": "冷峻犀利的技术专家，带点黑色幽默"
      },
      "rhythm": {
        "cut_profile": "偏快，信息密集，不喜欢拖沓"
      },
      "visual": {
        "palette": "暗色调，红色点缀，类似赛博朋克风格"
      },
      "language": {
        "style": "简洁直接，偶尔用技术术语，不啰嗦"
      }
    }
  }'
```

返回完整的 PersonaManifest，自动保存到 `personas/my_new_persona/`。

> 如果想进行多轮对话，每次把已收集的 answers 传给 `generate-questions`，系统会根据已有信息追问缺失的维度。

---

## 场景四：迭代优化 — 调校已有 Persona

> 适用：跑完管线后觉得效果不对，用自然语言微调

```bash
curl -X POST http://localhost:8000/api/persona/forge/refine \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "my_academic_style",
    "feedback": "学术味太重了，普通观众听不懂。节奏也太慢了，感觉拖沓。"
  }'
```

系统会自动：
1. 识别 "学术味太重" → 降低 `language.academic_density`
2. 识别 "节奏太慢" → 降低 `rhythm.base_shot_duration_ms`

**常用反馈及效果**：

| 反馈 | 影响参数 | 典型调整 |
|------|---------|---------|
| "节奏太慢了" | `base_shot_duration_ms` | 7000 → 4000 |
| "太学术了" | `academic_density` | 0.15 → 0.06 |
| "太花哨了" | `animation_styles.text_intro` | `typewriter_glitch` → `minimal_fade` |
| "开场不够冲击力" | `rhythm.surge_sections` | 追加 hook 段 |
| "BGM 太吵了" | `audio.bgm_slots` | 替换为更安静的风格 |
| "画面太亮了" | `visual.palette` | 切换到暗色调方案 |

> 离线模式下返回原 Persona 不变。需要 API key 才能做 LLM 驱动的精确调整。

---

## 验证和使用生成的 Persona

### 查看生成的配置

```bash
# 列出所有 Persona
curl http://localhost:8000/api/persona/list

# 查看完整配置
curl http://localhost:8000/api/persona/my_tech_sarcastic
```

### 用于管线

直接用 `persona_id` 跑全流程：

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "my_tech_sarcastic",
    "category_plugin_id": "digital_review",
    "topic": "某品牌新手机发热问题深度分析"
  }'
```

### 手动调整

Persona 以 YAML 存储在 `personas/{id}/` 目录，可以用任何编辑器直接修改：

```bash
vim personas/my_tech_sarcastic/parameter.yaml
# 修改后立即生效，无需重启
```

---

## 推荐工作流

```
新 UP 主
  │
  ├── 1. from-prompt     ← 一句话描述风格，生成初版
  │
  ├── 2. from-script     ← 上传历史脚本，补充精确语言参数
  │
  ├── 3. 跑一次管线      ← 看效果
  │
  ├── 4. refine          ← 根据结果反馈调整
  │
  ├── 5. 手动编辑 YAML   ← 最终微调
  │
  └── 6. 保存版本        ← Persona 支持版本管理
```

---

## 常见问题

**Q: 没有 API key，PersonaForge 能用吗？**
能。所有端点都带离线回退。from-prompt 做关键词匹配，from-script 做基础文本统计。效果不如 LLM 模式精确，但可以生成可用初版。

**Q: 生成的 Persona 存在哪里？**
自动保存到 `personas/{persona_id}/` 目录。`persona.yaml` 是 manifest，`parameter.yaml` 是参数层。

**Q: 可以反复 refine 吗？**
可以。每次 refine 基于当前 Persona 做增量调整，保存为新版本。可以随时查看版本历史。

**Q: PersonaForge 和手动写 YAML 什么关系？**
互补。Forge 自动生成初版，手动 YAML 做最终微调。生成的 YAML 和手写的是完全相同的格式，可以互相替换。

**Q: 支持哪些输入格式？**
from-prompt 接受任意自然语言文本。from-script 接受 `txt` / `srt` / `md` 格式。srt 会自动去除时间轴标记。
