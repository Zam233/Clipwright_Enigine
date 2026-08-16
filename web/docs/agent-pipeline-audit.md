# Agent 绠＄嚎鍏ㄩ噺瀹¤鎶ュ憡 (Agent Pipeline Audit)

> 璁″垝锛歚.omo/plans/clipwright-agent-pipeline-audit.md`
> 鐘舵€侊細瀹炵幇瀹屾垚锛圵ave 0-2 鍏ㄩ儴 31 涓疄鐜?todo 宸蹭氦浠橈紱鏈€缁堥獙璇?wave 杩涜涓紝鎻愪氦鍝堝笇鍦ㄦ渶缁堥獙璇佸悗琛ュ叏锛?
## 鎽樿

瀵?Agent 鍓┚椹朵笌绠＄嚎缂栨帓鍋氬叏閲忓璁′慨澶嶏細**21 椤?Bug锛圔1-B21锛? 10 椤规晥鐜囬棶棰橈紙E1-E10锛? 12 椤圭伒娲绘€?瀹炵敤鎬х己鍙ｏ紙G1-G12锛屽叾涓?9 椤规湰鏈熷疄鐜帮紝3 椤硅 backlog锛?*锛岃鐩栧墠鍚庣涓や粨銆備慨澶嶅悗锛氱绾夸笉鍐嶉噸澶嶆墽琛岃川妫€銆佸闃呭弽棣堣兘鐪熸椹卞姩瑙勫垝涔﹂噸鍋氥€佷粠棣栭〉鍚姩涓嶅啀涓㈠け鏂囩/闊宠壊/閰嶉煶銆佺绾垮畬鎴愬悗瀵硅瘽涓嶅啀姘镐箙绂佺敤銆侀厤闊虫鐪熸浼犲叆绠＄嚎鍋氭椂闂村榻愩€丩LM 璋冪敤鏈夐噸璇曘€佽瑙夎瘑鍒湁缂撳瓨銆丼SE 閲嶈繛鍙潬銆佸彇娑堢绾垮彲鐢ㄣ€佸闃呰鍥炬湁"涓嶆弧鎰忊啋閲嶅仛"鍏ュ彛銆?
## 鑼冨洿

**In锛堟湰鏈燂級**锛欱1-B21 鍏ㄩ儴锛汦1-E10锛圗1=B1 鍚堝苟锛夛紱G2銆丟4 瀛愰泦銆丟5銆丟6銆丟7銆丟8銆丟9銆丟10銆?**Out锛坆acklog锛孧ust-NOT-Have锛?*锛欸3 鐪熸祦寮忋€丟11 BGM 绱犳潗婧愩€丟1 绌虹櫧鍖哄煙灞€閮ㄨ皟鐢ㄣ€丟4 鐧惧垎姣旈噸鏋勩€丟12 by-path 鐧藉悕鍗曟墿灞曘€佹柊绗笁鏂逛緷璧栥€佹椂闂寸嚎 JSON 濂戠害鍙樻洿銆佺牬鍧忔€?API 璺緞鍙樻洿銆?
## Bug 娓呭崟 (B1-B21)

| # | 浣嶇疆 | 闂 | 璇佹嵁 | 淇鐘舵€?| 娴嬭瘯 | 鎻愪氦鍝堝笇 |
|---|------|------|------|:--------:|------|:--------:|
| B1 | `services/pipeline_v2.py` | Quality Agent 姣忕绾块噸澶嶆墽琛岋紙DAG 缁?+ 鑷剤寰幆寮€澶达級鈫?娴垂涓€娆″畬鏁磋川妫€ | `_run_inner` 鑷剤 while 寰幆寮€澶存棤鏉′欢 `_run_agent("quality", 鈥?`锛岃€?DAG 鎵ц缁勫凡鍚?quality | 馃攧 淇涓?| `test_pipeline_v2_quality_once.py` | b0b42a0 |
| B2 | `src/features/agent/AgentPanel.tsx` | SSE 鏂嚎閲嶈繛缂洪櫡锛氫笉 close銆佸弻杩炴帴椋庨櫓銆?5 娆″仠姝?瀹堝崼澶辨晥 | BottomBar `es.onerror` L647-669 | 馃攧 淇涓?| `AgentPanel.test.tsx` (U5 鏇存柊) | 7bf9412 |
| B3 | `api/pipeline.py` `retry_agent` | retry 鍏ㄩ噺閲嶈窇锛堝０绉颁粠澶辫触 agent 鎭㈠锛屽疄涓?V1 鍏ㄩ噺锛夛紱鍓嶇闆惰皟鐢?| L213-244 `_orchestrator.run` 鍏ㄩ噺 | 馃攧 淇涓?| `test_pipeline_v2_retry_from_agent.py` | 8dbe203 |
| B4 | `api/pipeline.py` `regenerate_scene` | 鍦烘櫙閲嶇敓鎴愭寜绱㈠紩浣嶇疆鏇挎崲锛岀粨鏋勪笉涓€鑷村嵆閿欎綅锛涗笉娉ㄥ叆瑙勫垝涔︼紱鍓嶇闆惰皟鐢?| L247-292 | 馃攧 淇涓?| 鏇挎崲/绉婚櫎娴嬭瘯 | 8dbe203 |
| B5 | `api/pipeline.py` `run_single_agent` | `/step/{agent}` 鍋囧崟姝ワ紙璺戝叏绠＄嚎鍐嶆娊缁撴灉锛夛紱鍓嶇闆惰皟鐢?| L320-336 | 馃攧 淇涓?| deprecated 鏂█ | 8dbe203 |
| B6 | `services/requirements_service.py` chat | 瑙勫垝涔﹀弽棣堥棴鐜柇瑁傦細鍙嶉 鈫?鍥?gathering 鈫?鍙噸鐢熸垚绠€鎶?| plan_ready 鍒嗘敮闈炵‘璁よ矾寰?| 馃攧 淇涓?| `test_requirements_plan_feedback.py` | 8f9b087 |
| B7 | `ReviewPanel.tsx` vs `api/requirements.py` | 瓒呮椂鍏紡鍓嶅悗绔笉涓€鑷达紱SSE max_wall=7200s < 鍚庣瓒呮椂 | ReviewPanel L119 vs proceed L229 | 馃攧 淇涓?| ReviewPanel 鍗曟祴 | 2af9957 |
| B8 | `pipeline_v2._persist_state` | 鎴柇鎶?dict/list 瀛樻垚瀛楃涓?鈫?Mongo 褰㈢姸婕傜Щ | L908-910 | 馃攧 淇涓?| `test_pipeline_v2_persist_shape.py` | b0b42a0 |
| B9 | `pipeline_v2._run_registry` | 杩愯娉ㄥ唽琛ㄦ棤鐣屽闀?| L50-52 | 馃攧 淇涓?| `test_pipeline_v2_registry_bound.py` | b0b42a0 |
| B10 | `src/services/api/*.ts` | 鍓嶇姝讳唬鐮?鏈帴绾匡細retry/regenerateScene/step/runV2/predictScript/predictMaterial 闆惰皟鐢?| 鍏ㄤ粨 grep | 馃攧 淇涓?| tsc 0 errors | 543907c |
| B11 | `useRequirementsAutoStart.ts` | topic 娑堣垂杩囨棭锛歩nit 鍓嶆竻绌猴紝闈炵绾块敊璇涪澶遍€夐 | L31 | 馃攧 淇涓紙骞跺叆 B15锛?| 鈥?| fd67f55 |
| B12 | `pipeline_v2._build_input("audio")` | V2 蹇界暐鍓嶇 voice_id/auto_dub锛堢‖缂栫爜 auto_dub=True銆乿oice_id 浠呭彇 persona锛?| L683-692 | 馃攧 淇涓?| `test_pipeline_v2_audio_config.py` | b0b42a0 |
| B13 | `api/pipeline.py` | V1 鍚屾绔偣 `/run`銆乣/run-v2` 鍓嶇闆朵娇鐢?| 鍓嶇 grep | 馃攧 淇涓?| deprecated 鏂█ | 8dbe203 |
| B14 | `ReviewPanel.tsx` sendFeedback catch | 璇锋眰澶辫触涓€寰嬭皫绉?锛堢绾挎ā寮忥級宸茶褰曞弽棣? | L94-98 | 馃攧 淇涓?| `ReviewPanel.test.tsx` | fd67f55 |
| B15 | `useRequirementsAutoStart.ts` | 鍙?user 娑堟伅鍐椾綑 | L36銆丩59 | 馃攧 淇涓?| `useRequirementsAutoStart.test.tsx` | fd67f55 |
| B16 | `EditorPage.tsx` + `HomePage.launch` | 銆愰噸澶с€憀aunch 鍙傛暟琚?resetProject() 娓呯┖涓旀湭鎭㈠ | EditorPage L46-68 | 馃攧 淇涓?| `EditorPage.test.tsx` | d54d591 |
| B17 | `AgentPanel.tsx`/`ReviewPanel.tsx` | 銆愰噸澶с€戠绾垮畬鎴愬悗闇€姹傚璇濊緭鍏ユ姘镐箙闅愯棌锛坰tatus 鍗?pipeline_running锛屾棤澶嶄綅锛?| AgentPanel L352 | 馃攧 淇涓?| `AgentPanel.test.tsx` | 7bf9412 |
| B18 | `EditorLayout.tsx` | Agent 闈㈡澘瀹藉害涓嶅彲璋冿細panels.agent 鐨?divider 鏃?onMouseDown/onPointerDown | L204 | 馃攧 淇涓?| `panel-divider.spec.ts` 鎴?vitest | c099281 |
| B19 | `useBackendHealth.ts` | 鍚庣鍋ュ悍妫€鏌ヤ竴娆℃€э紙鏃犺疆璇?蹇冭烦锛?| 鍏ㄦ枃浠?| 馃攧 淇涓?| `useBackendHealth.test.tsx` | 7bf9412 |
| B20 | `api/project.py` `create_project` | ProjectCreateRequest.agent_state 琚拷鐣?| L78-89 | 馃攧 淇涓?| `test_project_agent_state.py` | 66a5202 |
| B21 | `DubView.tsx` + `useRequirementsAutoStart.ts` + `projectStore` | 銆愰噸瑕併€戦厤闊虫鏂摼锛欴ubView 鏈湴 state锛宻etDubSegments 闆惰皟鐢紱autoStart extra 涓嶄紶 dub_segments | DubView L24锛沘utoStart L46-53 | 馃攧 淇涓?| `DubView.test.tsx` | fd67f55 |

## 鏁堢巼闂娓呭崟 (E1-E10)

| # | 浣嶇疆 | 闂 | 淇鐘舵€?| 娴嬭瘯 | 鎻愪氦鍝堝笇 |
|---|------|------|:--------:|------|:--------:|
| E1 | `pipeline_v2.py`锛?B1锛?| quality 鍙岃窇锛?1 娆″畬鏁磋川妫€/绠＄嚎 | 馃攧 淇涓?| `test_pipeline_v2_quality_once.py` | b0b42a0 |
| E2 | `requirements_service._generate_plan` | 鍚屼竴绠€鎶ラ噸澶嶇‘璁ら噸澶嶈窇 StructureAgent锛屾湭澶嶇敤 raw_scenes | 馃攧 淇涓?| `test_requirements_plan_feedback.py` | 8f9b087 |
| E3 | `services/llm.py` | 绠＄嚎 Agent LLM 璋冪敤鏃犻噸璇曪紱transient 澶辫触 鈫?鑷剤鍏ㄩ摼璺噸鍋?| 馃攧 淇涓?| `test_llm_retry.py` | 0f1d620 |
| E4 | SSE stream | 鏂嚎閲嶈繛鍏ㄩ噺閲嶆斁锛?000 鏉?鈮?MB锛?| 锛堟帴鍙楃幇鐘讹紝闈炴湰鏈燂級 | 鈥?| 鈥?|
| E5 | `_handle_gathering`/`_generate_plan` | 姣忔潯 chat 娑堟伅 RAG 妫€绱?+ Persona 涓婁笅鏂囧簭鍒楀寲锛岀粨鏋滄瘡娑堟伅涓㈠純 | 馃攧 淇涓?| `test_requirements_session_cache.py` | 8f9b087 |
| E6 | `services/trace.py` | 姣忔 get_events 鍏ㄩ噺杩囨护 + trim 澶嶅埗 | 馃攧 淇涓?| `test_trace_since.py` | 0f1d620 |
| E7 | `agentStore.addLogEntry` + `LogPanel` | 鏃ュ織姣忔杩藉姞 O(n) 澶嶅埗 + 鏃犱笂闄?DOM 娓叉煋 | 馃攧 淇涓?| `agentStore.test.ts` | c099281 |
| E8 | `animation_agent._build_image_semantic_index` | 姣忓浘 1 娆¤瑙?LLM銆佹棤缂撳瓨 | 馃攧 淇涓?| `test_vision_cache.py` | 0f1d620 |
| E9 | `services/vision.py` | 瑙嗚璇嗗埆鏃犵粨鏋滅紦瀛?| 馃攧 淇涓?| `test_vision_cache.py` | 0f1d620 |
| E10 | `material_agent._validate_via_vision_llm` | 瑙嗚鏍￠獙姣忓€欓€?1 娆″妯℃€佽皟鐢紝鏃?URL鈫抯core 缂撳瓨 | 馃攧 淇涓?| `test_material_vision_cache.py` | 0f1d620 |

## 鐏垫椿鎬?瀹炵敤鎬х己鍙ｆ竻鍗?(G1-G12)

| # | 缂哄彛 | 鏈湡澶勭疆 | 淇鐘舵€?| 娴嬭瘯 | 鎻愪氦鍝堝笇 |
|---|------|---------|:--------:|------|:--------:|
| G1 | 灞€閮ㄩ噸鍋氬悕涓嶅壇瀹?| 涓夌鐐瑰缃?+ 鍙嶉闂幆锛圔6锛? 瀹￠槄閲嶅仛鍏ュ彛锛圙10锛?| 馃攧 淇涓?| 鈥?| 8f9b087 |
| G2 | 鏃犲彇娑?鏆傚仠绠＄嚎 | **瀹炵幇**锛歝ancel 绔偣锛堝崗浣滃紡锛? 鍓嶇鍙栨秷鎸夐挳 | 馃攧 淇涓?| `test_pipeline_cancel.py` | 2af9957 |
| G3 | 闈炵湡娴佸紡 | **Out锛坆acklog锛?*锛歭lm.py `stream` 淇濇寔 False锛沗stream_chat` known-limitation 娉ㄩ噴涓嶅姩 | 鈥?| 鈥?| 鈥?|
| G4 | 杩涘害鏉￠潪鐪熷疄杩涘害 | 閮ㄥ垎锛欱ottomBar 鏄剧ず褰撳墠 Agent 娲诲姩锛堜笉鍋氱櫨鍒嗘瘮閲嶆瀯锛?| 馃攧 淇涓?| `AgentPanel.test.tsx` | 7bf9412 |
| G5 | 绂荤嚎/鍦ㄧ嚎娣锋穯 | 绂荤嚎妯箙 + 鍛ㄦ湡鎬у仴搴锋鏌?+ 鍖哄垎鏈繛鎺?鎵ц澶辫触 | 馃攧 淇涓?| `useBackendHealth.test.tsx` | 7bf9412 |
| G6 | HomePage 鏃犳櫤鑳介鍒?| 鎺ョ嚎 predict-script | 馃攧 淇涓?| `HomePage.test.tsx` | 543907c |
| G7 | 闇€姹傚璇濇棤鍙傝€冩枃浠朵笂浼?| 鎺ョ嚎 upload | 馃攧 淇涓?| `AgentPanel.test.tsx` | fd67f55 |
| G8 | 浼氳瘽鎭㈠浠呴潬 localStorage | 鎺ョ嚎 getSession | 馃攧 淇涓?| `EditorPage.test.tsx` | d54d591 |
| G9 | PipelineAdminPage 鍋囨垚鏈?+ 鍙 | 鐪熷疄鎴愭湰 + 閲嶈瘯鎸夐挳 | 馃攧 淇涓?| `PipelineAdminPage.test.tsx` | 543907c |
| G10 | 瀹￠槄瑙嗗浘鏃?涓嶆弧鎰忊啋閲嶅仛"鍏ュ彛 | **瀹炵幇** | 馃攧 淇涓?| `TimelineDiffView.test.tsx` | 543907c |
| G11 | BGM 浠呭缓璁棤鐪熷疄閰嶄箰 | **Out锛坆acklog锛?*锛欰udioAgent 浠?metadata 寤鸿淇濇寔 | 鈥?| 鈥?| 鈥?|
| G12 | 鏈湴绱犳潗棰勮 403 闄嶇骇 | 鏂囨。鍖栭檺鍒讹紝璁板綍涓嶆敼 | 璁板綍 | 鈥?| 鈥?|

## 淇璇存槑涓庡彇鑸?
- **B3**锛坮etry 浠庡け璐?Agent 鎭㈠锛夛細`PipelineOrchestratorV2.run_from_agent` 閲嶅缓 result_data 鈥斺€?浠?`state.steps[]` 鎸夋墽琛岄『搴忓彇鐩爣 agent 涔嬪墠鎵€鏈?`status==completed` 涓?result 闈炵┖鐨勬楠わ紝鎸?`_merge_agent_result` 璇箟閲嶆斁锛涘啀鎵ц鐩爣 + 涓嬫父鑱斿姩锛涙棤鍙敤鍓嶇疆缁撴灉 鈫?400銆?- **B4**锛坮egenerate-scene锛夛細鎸?clip id 璇箟鍖归厤鏇挎崲锛坢etadata.source_title/clip.text/start_sec 鏈€杩戦偦锛夛紱鑻ュ疄鐜版垚鏈秴闄愬垯绉婚櫎绔偣 + 鍓嶇绉婚櫎瀹㈡埛绔嚱鏁般€?- **G2**锛堝彇娑堢绾匡級锛?*鍗忎綔寮忓彇娑?*鈥斺€斾笉寮哄埗涓柇 in-flight LLM锛坅syncio.to_thread 涓嶅彲鍙栨秷锛夛紝鍦ㄤ笅涓€涓?agent 杈圭晫鐢熸晥锛涘彇娑堝悗 `state.status=CANCELLED` + 钀藉簱 + result 鍐?cancelled + SSE `cancelled` 浜嬩欢銆?- **B7**锛氳秴鏃跺叕寮忕粺涓€涓?`max(1800, audio脳6, scene脳360)`锛汼SE `max_wall` 浠?`extra_params.pipeline_timeout_sec` 鍔ㄦ€佸寲锛堝洖閫€榛樿 7200+600 浣欓噺锛夈€?
## 娴嬭瘯涓庤川閲忓熀绾?
- 鍚庣鍩虹嚎锛歚python -m pytest tests/ -q` 鈫?**858 passed**锛堣鍒掓椂 ~699锛屾紓绉诲悗浠ュ疄娴嬩负鍑嗭級銆?- 鍓嶇鍩虹嚎锛歚npm run test` 鈫?**225 passed**锛?5 鏂囦欢锛夈€?- 鏈€缁堥獙璇?wave锛氬悗绔叏閲?pytest + ruff锛涘墠绔?tsc 0 errors + vitest 鍏ㄧ豢 + build + lint + Playwright E2E锛坔ermetic mock锛岃矾寰勯敋瀹氭鍒?`/https?:\/\/[^/]+\/api\//`锛夈€?- 鎵嬪姩鑱旇皟鐐癸紙QA 璁板綍锛夛細B16 棣栭〉鍚姩 鈫?纭瀹炴柦 鈫?鍚庣鏀跺埌 script_text/voice_id/audio_path锛汢17 绠＄嚎瀹屾垚鍚庤緭鍏ユ鎭㈠锛汢21 閰嶉煶娈佃繘鍏ヨ鍒掍功鍦烘櫙瀵归綈锛汫2 鍙栨秷鍚庣绾跨粓鎬?cancelled銆?
## 鎻愪氦璁板綍

> 瀹屾垚鍚庤ˉ鍏ㄤ袱浠撴彁浜ゅ搱甯岋紙鍚庣鎸夐€昏緫缁勩€佸墠绔寜 wave 鍒嗙粍锛夛紝骞舵牳瀵?`git log` 涓庝笅琛ㄤ竴涓€瀵瑰簲銆?
| 浠?| 鎻愪氦淇℃伅锛堝墠缂€锛?|
|----|------------------|
| 鍚庣 | `fix(pipeline): run quality agent once per pipeline` 路 `fix(pipeline): preserve shared_data shape on truncation` 路 `fix(pipeline): bound run registry` 路 `fix(pipeline): honor frontend voice_id/auto_dub in v2` 路 `feat(pipeline): retry from failed agent (v2)` 路 `fix(pipeline): regenerate scene by clip id` 路 `refactor(pipeline): deprecate v1 sync endpoints` 路 `feat(requirements): regenerate plan from review feedback` 路 `fix(pipeline): align timeout formula and SSE max_wall` 路 `feat(llm): exponential backoff retry for transient errors` 路 `perf(requirements): cache RAG + persona context per session` 路 `perf(trace): index-based since query` 路 `perf(vision): cache image analysis by path+mtime` 路 `perf(material): cache vision validation scores` 路 `feat(pipeline): cooperative cancel endpoint` 路 `fix(project): persist agent_state on create` |
| 鍓嶇 | `fix(editor): restore launch params after resetProject` 路 `fix(agent): reset requirements status on pipeline finish` 路 `fix(agent): reliable SSE reconnect with hard cap` 路 `fix(agent): wire dub segments into requirements/pipeline` 路 `fix(agent): distinguish offline vs request failure + heartbeat` 路 `refactor(api): drop dead pipeline clients, keep wired ones` 路 `feat(home): script intelligence prediction` 路 `feat(agent): reference file upload in requirements chat` 路 `feat(editor): restore requirements session from backend` 路 `feat(admin): real cost + retry failed runs` 路 `feat(agent): rework request from diff review` 路 `perf(agent): cap log entries and collapse groups` 路 `feat(agent): show current agent activity in bottom bar` 路 `fix(layout): resizable agent panel divider` 路 `docs(agent): pipeline-agent audit report` |

