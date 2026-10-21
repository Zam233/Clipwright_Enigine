# PV 引擎插件（`pv_engine`）

把 [eternal-questioning-pv](https://github.com/Zam233/eternal-questioning-pv) 的逐帧渲染引擎
接成 Clipwright 里 **agent / 子 agent 可调用** 的能力。

> 引擎本身是什么：一支歌词 PV 的**可复现渲染器**——`t → 找到当前 SHOT → 逐条 CUE 求值
> → 后期 → 一帧 RGB`。全部设计写成数据（`shots_v20.json`），改一个数重渲一遍，成片就变；
> 自带 30+ 条硬规则审计（`audit.py`，退出码即结论）。

## 一、怎么接的

**用子进程适配，不用 import**：

1. 引擎是独立仓库，依赖（numpy/Pillow/OpenCV/SciPy，GPU 可选）不该绑死宿主进程；
2. `audit.py` 用**退出码**表达结论（0 = 全过），子进程最直接；
3. 崩了/超时只影响子进程。

适配层：`plugins/pv_engine/adapter.py`；工具与技能：`plugins/pv_engine/main.py`。

### 引擎目录解析顺序

1. 工具入参 `engine_dir`
2. 插件配置 `engine_dir`（后台插件配置页可填）
3. 环境变量 `CLIPWRIGHT_PV_ENGINE_DIR`

引擎目录里必须有 `_build/audit.py` 与 `_build/pv/build.py`（缺哪个会报哪个）。

## 二、贡献的能力

| 能力 | 类型 | 权限档 | 干什么 |
|---|---|---|---|
| `pv_engine_probe` | Tool | `read` | 体检 shots 数据：镜头数、时间覆盖、空隙（可离线） |
| `pv_engine_audit` | Tool | `read` | 跑引擎规则审计（R01–R36），**退出码即结论** |
| `pv_engine_stills` | Tool | `write_local` | 抽静帧供构图复核（需要引擎 assets） |
| `pv_engine_review` | Skill | — | 把 probe → audit（→ stills）串成一步给 agent 用 |

**对 agent 的可见性**：三个工具都声明 `agent_callable = True`，并用 `is_configured()`
做了门控——**未配置引擎目录时不会出现在阶段 agent 的工具面**（避免模型拿着工具白烧 token）；
主 agent 的 kernel 工具面仍可见，调用时如实返回"未配置"。

## 三、真机证据（引擎就绪时）

```
probe  → success：53 shots / 291.13 s covered / gaps: []
audit  → exit_code 1，verdict=fail（stdout 里 WARN R36 + "FAIL 1 条"）
```

注意：那次 `audit` 的 1 条 FAIL 是**引擎仓库自身 `shots_v20.json` 的状态**，不是插件故障——
插件的作用就是把结论如实转达（退出码 1 → `verdict: fail`），不粉饰成"通过"。

## 四、诚实边界

- 引擎仓库**不含版权素材**（MET 公版版画与 AI 人物层都在 `.gitignore`）。因此：
  `probe` / `audit` 可离线跑；`stills` / `frames` 需要先把素材准备好
  （`_build/fetch_met.mjs` 下载 MET 公版、`_build/gen.mjs` 生成 AI 人物层，后者需要
  `IMG_BASE` / `IMG_KEY`），否则引擎会报缺图，插件如实报错。
- 本插件**不含** `frames`（整片渲染）工具：它需要 GPU 编码与素材，属"costly"档，
  等真正需要时再加，并配预算闸。

## 五、怎么用

```powershell
# 1) 备好引擎仓库（或指向已有克隆）
$env:CLIPWRIGHT_PV_ENGINE_DIR = "K:\eternal-questioning-pv"

# 2) 在后台插件页启用 pv_engine（或直接调工具）
# 3) 调用示例（伪代码）
pv_engine_probe(shots="shots_v20.json")
pv_engine_audit(shots="shots_v20.json")
pv_engine_stills(times=[20, 90, 158], shots="shots_v20.json", out_dir="stills")
pv_engine_review(shots="shots_v20.json", stills_times=[20, 90])
```

测试：`tests/clipwright/test_pv_engine_plugin.py`（27 项用假引擎覆盖适配层与门控 +
1 项真引擎 probe 集成；设了 `CLIPWRIGHT_PV_ENGINE_DIR` 才会跑集成用例）。
