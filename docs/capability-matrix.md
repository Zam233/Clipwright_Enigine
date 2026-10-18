# 能力矩阵（自动生成，勿手改）

> 生成命令：`python -m clipwright.services.capability_matrix`
> 事实来源：`ToolRegistry` / `SkillRegistry` 实际注册表 + `capability_matrix.META_CAPABILITIES`。

## 汇总

| 指标 | 值 |
|---|---|
| 能力总数 | 57 |
| 按类型 | {"tool": 49, "skill": 4, "pipeline": 1, "subagent": 1, "meta": 2} |
| 按权限档 | {"read": 14, "write_local": 29, "costly": 11, "dangerous": 3} |
| LLM 可调用（当前） | 12（21%） |
| 依赖可用 | 55 |
| 支持 dry-run（当前） | 1 |
| dangerous 档 | video_download, run_python, run_shell |

## 明细

| 能力 | 类型 | 权限档 | 可用 | LLM 可见 | 幂等 | dry-run | 备注 |
|---|---|---|---|---|---|---|---|
| `describe_llm_mg` | tool | costly | ✓ | ✓ | ✗ | ✗ |  |
| `event_spot` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `generate_text_video` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `text_to_speech` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `vision_llm` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `voice_clone` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `web_fetch` | tool | costly | ✗ | ✓ | ✗ | ✗ |  |
| `web_search` | tool | costly | ✗ | ✓ | ✗ | ✗ |  |
| `whisper_transcribe` | tool | costly | ✓ | ✗ | ✗ | ✗ |  |
| `video_download` | tool | dangerous | ✓ | ✗ | ✗ | ✗ |  |
| `audio_silence_detect` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `black_frame_detect` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `bpm_detect` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `content_query` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `face_detect` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `frame_validator` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `list_animations` | tool | read | ✓ | ✓ | ✓ | ✗ |  |
| `material_filter` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `media_probe` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `project_memory` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `scene_detect` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `semantic_match` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `subtitle_overflow` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `timeline_view` | tool | read | ✓ | ✗ | ✓ | ✗ |  |
| `audio_extract` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `audio_mix` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `audio_normalize` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `audio_replace` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `background_remove` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `chroma_key` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `color_correct` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `color_match` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `effect_vignette` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `lut_apply` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `speed_ramp` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `subtitle_burn` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `text_diagram` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `tracking_text` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `transition_apply` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `typewriter_animation` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_blur` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_concat` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_crop` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_overlay` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_speed` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_stabilize` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_thumbnail` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `video_trim` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `watermark` | tool | write_local | ✓ | ✗ | ✗ | ✗ |  |
| `analyze_audio_rhythm` | skill | write_local | ✓ | ✓ | ✗ | ✗ | 组合能力（4 个内置 Skill） |
| `analyze_video_structure` | skill | write_local | ✓ | ✓ | ✗ | ✗ | 组合能力（4 个内置 Skill） |
| `dub_script` | skill | write_local | ✓ | ✓ | ✗ | ✗ | 组合能力（4 个内置 Skill） |
| `generate_captions` | skill | write_local | ✓ | ✓ | ✗ | ✗ | 组合能力（4 个内置 Skill） |
| `run_pipeline` | pipeline | costly | ✓ | ✓ | ✗ | ✓ | 老 6 阶段管线，降级为可调用能力；常规套路/预算敏感时应优先选它 |
| `spawn_subagent` | subagent | costly | ✓ | ✓ | ✗ | ✗ | 星型拓扑、深度 1；只读角色不需审核门，带执行权限需两级审核 |
| `run_python` | meta | dangerous | ✓ | ✓ | ✗ | ✗ | 同上；脚本落临时文件、默认禁网 |
| `run_shell` | meta | dangerous | ✓ | ✓ | ✗ | ✗ | 经 ExecutionGateway：硬底线标记 + 独立 LLM 判定 + 两级确认 |

> 覆盖度是 C1 的硬门槛（PRD A7）：`LLM 可见` 这一列是**旧的** `agent_callable` 标记，不代表 Kernel 实际能看到什么——真正的工具面见下一节。

## Kernel 工具面（C1）

> 口径：`read` / `write_local` / `costly` 可被 Kernel 直调；`dangerous` 只能经执行网关（PRD F6 红线 2/5）。

| 指标 | 值 |
|---|---|
| 直调档位 | read, write_local, costly |
| 工具面条目 | 48（可用 46） |
| 按档位 | {"costly": 9, "read": 14, "write_local": 25} |
| 覆盖率（可用安全工具） | 100%（池 46 项） |
| 真实 dry-run | 0 项（其余走结构预检） |
| 结构预检 | 48 项（参数/路径可验证，不验滤镜语义） |
| 排除的 dangerous | video_download, run_python, run_shell |

