/* 需求 Agent 工作流 — 集成到剪辑工作台 */
/* 依赖: marked.js (全局), app.js (api function) */

let _ewSessionId = "";
let _ewPollTimer = null;

function ewRenderMD(text) {
  if (!text) return "";
  try { if (typeof marked !== "undefined") return marked.parse(text); } catch(e) {}
  return text.replace(/\n/g, "<br>");
}

function ewToast(msg, type) {
  const el = document.getElementById("reqToast") || document.getElementById("ewToast");
  if (!el) { alert(msg); return; }
  el.textContent = msg;
  el.className = "toast toast-" + (type || "info");
  el.style.display = "block";
  setTimeout(() => { el.style.display = "none"; }, 4000);
}

function ewScrollBottom() {
  const el = document.getElementById("ewMessages");
  if (el) el.scrollTop = el.scrollHeight;
}

function ewShowTyping(show) {
  const el = document.getElementById("ewTypingIndicator");
  if (!el) return;
  el.style.display = show ? "flex" : "none";
  if (show) ewScrollBottom();
}

function ewAddBubble(role, content, metadata) {
  const area = document.getElementById("ewMessages");
  if (!area) return;
  const isUser = role === "user";
  const wrapper = document.createElement("div");
  wrapper.className = "chat-msg " + (isUser ? "user" : "ai") + " req-msg";

  if (isUser) {
    wrapper.textContent = content;
  } else {
    let md = document.createElement("div");
    md.className = "md-content";
    md.innerHTML = ewRenderMD(content);
    wrapper.appendChild(md);
  }

  const label = document.createElement("div");
  label.className = "msg-label";
  label.textContent = isUser ? "你" : "需求助手";
  wrapper.appendChild(label);

  const typing = document.getElementById("ewTypingIndicator");
  if (typing) area.insertBefore(wrapper, typing);
  else area.appendChild(wrapper);
  ewScrollBottom();
}

function ewUploadRef() {
  const input = document.getElementById("ewRefFile");
  if (!input || !input.files.length) return;
  const names = Array.from(input.files).map(f => f.name).join(", ");
  const el = document.getElementById("ewRefName");
  if (el) el.textContent = names;
}

function ewChatUpload() {
  const input = document.getElementById("ewChatFile");
  if (!input || !input.files.length) return;
  const names = Array.from(input.files).map(f => f.name).join(", ");
  const el = document.getElementById("ewChatFileName");
  if (el) el.textContent = names;
}

async function ewStartSession() {
  const topic = document.getElementById("ewTopic").value.trim();
  if (!topic) { ewToast("请输入创作主题", "error"); return; }
  const data = {
    topic,
    category_plugin_id: document.getElementById("ewPluginId").value,
    persona_id: document.getElementById("ewPersonaId").value.trim(),
    script_text: document.getElementById("ewScript").value.trim(),
    audio_duration_sec: parseFloat(document.getElementById("ewDuration").value) || 300,
    audio_file_path: document.getElementById("ewAudioPath").value.trim() || "",
  };
  const { ok, data: result } = await api("POST", "/api/requirements/init", data);
  if (!ok) { ewToast("初始化失败", "error"); return; }
  _ewSessionId = result.session_id || result._id;
  document.getElementById("ewInitForm").style.display = "none";
  document.getElementById("ewChatArea").style.display = "flex";
  ewRenderMessages(result.messages || []);
  ewUpdateStatus(result.status);

  // 自动触发需求 Agent
  const ewScript = document.getElementById("ewScript").value.trim();
  const ewTopic = document.getElementById("ewTopic").value.trim();
  const ewDur = document.getElementById("ewDuration").value;
  let firstMsg = "我要做一个视频。主题：" + ewTopic;
  if (ewScript) firstMsg += "，文稿：" + ewScript;
  firstMsg += "，预估时长：" + ewDur + "秒";
  firstMsg += "。请直接给我一份完整的创作方案草案，包括标题、概述、风格建议和结构建议。不确定的部分用“待定”代替，先出草案再调整。";
  ewSilentTrigger(firstMsg);
}

function ewRenderMessages(messages) {
  const area = document.getElementById("ewMessages");
  const typing = document.getElementById("ewTypingIndicator");
  area.innerHTML = "";
  if (typing) area.appendChild(typing);
  if (!messages) return;
  for (const msg of messages) {
    if (!msg || !msg.role || !msg.content) continue;
    ewAddBubble(msg.role, msg.content, msg.metadata);
  }
}

function ewSilentTrigger(text) {
  if (!text || !_ewSessionId) return;
  ewShowTyping(true);
  api("POST", "/api/requirements/chat", {
    session_id: _ewSessionId, message: text,
  }).then(({ok, data}) => {
    ewShowTyping(false);
    if (!ok) return;
    ewRenderMessages(data.messages || []);
    ewUpdateStatus(data.status);
    if (data.creative_brief && data.creative_brief.title) {
      ewShowBrief(data.creative_brief);
    }
    if (data.production_plan) ewShowPlan(data.production_plan);
    else if (data.status === 'plan_ready' || data.status === 'plan_confirmed') ewFetchPlan();
  }).catch(() => ewShowTyping(false));
}

async function ewSendMessage() {
  const input = document.getElementById("ewChatInput");
  const text = input.value.trim();
  if (!text || !_ewSessionId) return;
  input.value = "";
  input.disabled = true;
  ewAddBubble("user", text);
  ewShowTyping(true);
  const { ok, data } = await api("POST", "/api/requirements/chat", {
    session_id: _ewSessionId, message: text,
  });
  input.disabled = false;
  ewShowTyping(false);
  if (!ok) { ewToast("发送失败", "error"); return; }
  ewRenderMessages(data.messages || []);
  ewUpdateStatus(data.status);
  if (data.creative_brief && data.creative_brief.title) {
    ewShowBrief(data.creative_brief);
  }
  if (data.status === "plan_ready" || data.status === "plan_confirmed") ewFetchPlan();
}

function ewUpdateStatus(status) {
  const badge = document.getElementById("ewStatusBadge");
  if (!badge) return;
  const labels = {
    gathering: "需求收集", brief_ready: "方案待确认", brief_confirmed: "已确认",
    planning: "生成中", plan_ready: "规划书待确认", plan_confirmed: "已确认",
    pipeline_running: "制作中", pipeline_done: "已完成",
  };
  badge.textContent = labels[status] || status;
}

function ewBackToChat() {
  document.getElementById("ewPlanPanel").style.display = "none";
  document.getElementById("ewChatArea").style.display = "flex";
}

async function ewFetchPlan() {
  const { ok, data } = await api("GET", "/api/requirements/plan/" + _ewSessionId);
  if (!ok) return;
  ewShowPlan(data);
}

function ewShowBrief(brief) {
  var panel=document.getElementById("ewPlanPanel");
  var contentEl=document.getElementById("ewPlanContent");
  var nav=document.getElementById("ewPlanNav");
  var stats=document.getElementById("ewPlanStats");
  if(!panel||!contentEl)return;
  panel.style.display="flex";
  if(!brief||!brief.title){contentEl.innerHTML="<div class=\\\"note\\\">方案生成中...</div>";return;}
  var lines=[];
  lines.push("# "+(brief.title||"创作方案"));
  lines.push("");
  lines.push("## 概述");
  lines.push(brief.overview||"待定");
  lines.push("");
  lines.push("| 项目 | 内容 |");
  lines.push("|------|------|");
  lines.push("| 目标受众 | "+(brief.target_audience||"待定")+" |");
  lines.push("| 核心信息 | "+(brief.core_message||"待定")+" |");
  lines.push("| 风格方向 | "+(brief.style_direction||"待定")+" |");
  lines.push("| 结构建议 | "+(brief.structure_suggestion||"待定")+" |");
  lines.push("| 预估时长 | "+(brief.duration_estimate||"待定")+" |");
  if(brief.key_elements&&brief.key_elements.length){
    lines.push("");lines.push("## 关键元素");
    brief.key_elements.forEach(function(e){lines.push("- "+e);});
  }
  if(brief.special_requirements&&brief.special_requirements.length){
    lines.push("");lines.push("## 特殊要求");
    brief.special_requirements.forEach(function(r){lines.push("- "+r);});
  }
  contentEl.innerHTML=ewRenderMD(lines.join(String.fromCharCode(10)));
  if(stats)stats.textContent="方案草案";
  nav.innerHTML="";
  // 确认按钮
  var confirmBtn=document.getElementById("ewPlanConfirmBtn");
  if(confirmBtn){confirmBtn.textContent="确认方案";confirmBtn.onclick=function(){ewConfirmBrief();};confirmBtn.disabled=false;}
}

function ewShowPlan(plan) {
  var confirmBtn=document.getElementById("ewPlanConfirmBtn");
  if(confirmBtn){confirmBtn.textContent="确认并制作";confirmBtn.onclick=function(){ewConfirmPlan();};confirmBtn.disabled=false;}
  const panel = document.getElementById("ewPlanPanel");
  const content = document.getElementById("ewPlanContent");
  const nav = document.getElementById("ewPlanNav");
  const stats = document.getElementById("ewPlanStats");
  if (!panel || !content) return;
  panel.style.display = "flex";
  if (!plan || !plan.markdown) {
    content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text2)">⏳ 规划书正在生成中...</div>';
    nav.innerHTML = ""; return;
  }
  content.innerHTML = ewRenderMD(plan.markdown);
  if (stats) stats.textContent = (plan.scene_count || 0) + " 个场景 · 约 " + Math.round((plan.total_duration_sec || 0) / 60) + " 分钟";
  const headings = content.querySelectorAll("h1, h2, h3");
  if (headings.length > 0) {
    let navHtml = '<div style="padding:6px 0;font-weight:600;color:var(--accent);font-size:13px">目录</div>';
    headings.forEach(h => {
      const indent = h.tagName === "H1" ? "0" : h.tagName === "H2" ? "12px" : "24px";
      const id = "ph-" + Math.random().toString(36).slice(2);
      h.id = id;
      navHtml += '<div style="padding:3px 0 3px ' + indent + ';font-size:12px;cursor:pointer;color:var(--text2)" onclick="document.getElementById(\'' + id + '\').scrollIntoView({behavior:\'smooth\'})">' + (h.textContent || "") + "</div>";
    });
    nav.innerHTML = navHtml;
  }
}

async function ewConfirmBrief() {
  if (!_ewSessionId) return;
  ewShowTyping(true);
  api("POST", "/api/requirements/chat", {
    session_id: _ewSessionId, message: "确认",
  }).then(({ok, data}) => {
    ewShowTyping(false);
    if (!ok) return;
    ewRenderMessages(data.messages || []);
    ewUpdateStatus(data.status);
    if (data.status === "plan_ready" || data.status === "plan_confirmed") {
      ewFetchPlan();
    }
  }).catch(() => ewShowTyping(false));
}

async function ewConfirmPlan() {
  if (!_ewSessionId) return;
  const btn = document.getElementById("ewPlanConfirmBtn");
  btn.disabled = true;
  btn.textContent = "启动中...";
  // 同步参数到剪辑工作台
  var topic=document.getElementById("ewTopic").value.trim();
  var script=document.getElementById("ewScript").value.trim();
  var pid=document.getElementById("ewPersonaId").value.trim();
  var plg=document.getElementById("ewPluginId").value;
  var aud=document.getElementById("ewAudioPath").value.trim();
  var dur=parseFloat(document.getElementById("ewDuration").value)||300;
  if(document.getElementById("ewTopic"))document.getElementById("ewTopic").value=topic;
  if(document.getElementById("ewScript"))document.getElementById("ewScript").value=script;
  if(document.getElementById("ewPersonaId"))document.getElementById("ewPersonaId").value=pid||"zam_knowledge_critical";
  if(document.getElementById("ewPluginId"))document.getElementById("ewPluginId").value=plg;
  if(document.getElementById("ewDuration"))document.getElementById("ewDuration").value=dur;
  if(document.getElementById("ewAudioPath")&&aud)document.getElementById("ewAudioPath").value=aud;
  // 切换到剪辑工作台视图
  var panel=document.getElementById("ewPlanPanel");
  if(panel)panel.style.display="none";
  ewToast("已切换到剪辑视图，管线即将启动", "success");
  setTimeout(function(){
    ewLog("需求已确认，启动视频制作管线...", "title");
    runAiEdit();
  }, 500);
}

function ewPollPipeline() {
  if (_ewPollTimer) clearInterval(_ewPollTimer);
  setEwProgress(5, "管线已启动");
  _ewPollTimer = setInterval(async () => {
    if (!_ewSessionId) { clearInterval(_ewPollTimer); return; }
    try {
      const { ok, data } = await api("GET", "/api/requirements/session/" + _ewSessionId);
      if (!ok) return;
      if (data.status === "pipeline_done" || data.status === "completed") {
        clearInterval(_ewPollTimer);
        setEwProgress(100, "完成");
        ewLog("视频制作完成!", "success");
        const topic = document.getElementById("ewTopic").value.trim();
        const script = document.getElementById("ewScript").value.trim();
        if (topic || script) runAiEdit();
      } else if (data.status === "error") {
        clearInterval(_ewPollTimer);
        ewLog("制作失败", "error");
      } else {
        const bar = document.getElementById("ewProgressBar");
        const cur = parseFloat(bar?.style?.width?.replace("%", "") || "10");
        setEwProgress(Math.min(cur + 5, 90), "制作中...");
      }
    } catch(e) {}
  }, 3000);
}

function ewRefreshSession() {
  if (!_ewSessionId) return;
  ewShowTyping(true);
  api("GET", "/api/requirements/session/" + _ewSessionId).then(({ok, data}) => {
    ewShowTyping(false);
    if (!ok) return;
    ewRenderMessages(data.messages || []);
    ewUpdateStatus(data.status);
    if (data.production_plan) ewShowPlan(data.production_plan);
  }).catch(() => ewShowTyping(false));
}
