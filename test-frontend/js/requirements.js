/* 需求工作台 v2 — MongoDB 持久化 + marked.js + 文件上传 + 本地草稿 */

let reqSessionId = '';
let reqPollTimer = null;
let reqToastTimer = null;

const STORAGE_KEY = 'clipwright_req_draft';

// ── 初始化 marked.js ──────────────────────────────

if (typeof marked !== 'undefined') {
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: function(code, lang) {
      if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
        try { return hljs.highlight(code, { language: lang }).value; } catch(e) {}
      }
      return code;
    }
  });
}

function renderMD(text) {
  if (!text) return '';
  try {
    if (typeof marked !== 'undefined') return marked.parse(text);
  } catch(e) {}
  // 降级
  return text.replace(/\n/g, '<br>');
}

// ── Toast 通知 ────────────────────────────────────

function reqToast(msg, type) {
  const el = document.getElementById('reqToast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast toast-' + (type || 'info');
  el.style.display = 'block';
  clearTimeout(reqToastTimer);
  reqToastTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);
}

// ── 本地草稿 ──────────────────────────────────────

function saveDraft(data) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch(e) {}
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch(e) { return null; }
}

function clearDraft() {
  try { localStorage.removeItem(STORAGE_KEY); } catch(e) {}
}

// ── 会话管理 ──────────────────────────────────────

function reqStartSession() {
  const topic = document.getElementById('reqTopic').value.trim();
  if (!topic) { reqToast('请输入创作主题', 'error'); return; }

  const audioPath = document.getElementById('reqAudioPath').value.trim();
  const script = document.getElementById('reqScript').value.trim();
  const dur = parseFloat(document.getElementById('reqDuration').value) || 300;

  // 预处理字幕切分
  _reqSttSegments = [];
  _reqCaptionSegments = [];
  if (script) _reqCaptionSegments = reqSplitScript();

  // 异步 STT 对齐
  const processAudio = async () => {
    if (audioPath && _reqCaptionSegments.length > 0) {
      const sttDur = await reqAlignStt(audioPath, _reqCaptionSegments);
      if (sttDur > 0) document.getElementById('reqDuration').value = Math.max(dur, Math.ceil(sttDur));
    }
  };

  const data = {
    topic,
    category_plugin_id: document.getElementById('reqCategory').value,
    persona_id: document.getElementById('reqPersona').value.trim(),
    script_text: script,
    audio_duration_sec: parseFloat(document.getElementById('reqDuration').value) || dur,
    audio_file_path: audioPath,
  };

  api('POST', '/api/requirements/init', data).then(({ok, data}) => {
    if (!ok) { reqToast('初始化失败: ' + (data.detail || JSON.stringify(data)), 'error'); return; }
    reqSessionId = data.session_id || data._id;
    document.getElementById('reqInitForm').style.display = 'none';
    document.getElementById('reqChatArea').style.display = 'block';
    renderReqMessages(data.messages || []);
    updateReqStatus(data.status);
    saveDraft({ session_id: reqSessionId, status: data.status });

    // 自动触发需求Agent
    const personaId = document.getElementById('reqPersona').value.trim();
    const catId = document.getElementById('reqCategory').value;
    const durVal = document.getElementById('reqDuration').value;
    let ctx = '我要做一个视频。主题：' + topic;
    if (script) ctx += '，文稿：' + script;
    // persona_id 已通过 init API 传递，服务端会自动解析并注入样式参数
    ctx += '，预估时长：' + durVal + '秒';
    ctx += '。请直接给我一份完整的创作方案草案，包括标题、概述、风格建议和结构建议。不确定的部分用“待定”代替，先出草案再调整。';
    // 隐式触发：不显示用户消息，只显示 Agent 回复
    reqSilentTrigger(ctx);
  }).catch(err => { reqToast('初始化失败: ' + err.message, 'error'); });

  processAudio();
}

function reqSilentTrigger(text) {
  if (!text || !reqSessionId) return;
  showTyping(true);
  api('POST', '/api/requirements/chat', {
    session_id: reqSessionId, message: text,
  }).then(({ok, data}) => {
    showTyping(false);
    if (!ok) { reqToast('需求分析失败', 'error'); return; }
    renderReqMessages(data.messages || []);
    updateReqStatus(data.status);
    if (data.creative_brief && data.creative_brief.title) {
      showReqBrief(data.creative_brief);
      document.getElementById('reqBriefArea').style.display = 'block';
    }
    if (data.production_plan) {
      showReqPlan(data.production_plan);
    } else if (data.status === 'plan_ready' || data.status === 'plan_confirmed') {
      fetchReqPlan();
    }
  }).catch(err => {
    showTyping(false);
    reqToast('需求分析失败: ' + err.message, 'error');
  });
}

function reqSendMessage() {
  const input = document.getElementById('reqInput');
  const text = input.value.trim();
  if (!text || !reqSessionId) return;

  input.value = '';
  input.disabled = true;
  addUserMsg(text);
  showTyping(true);

  api('POST', '/api/requirements/chat', {
    session_id: reqSessionId, message: text,
  }).then(({ok, data}) => {
    input.disabled = false;
    showTyping(false);
    if (!ok) { reqToast('发送失败: ' + (data.detail || JSON.stringify(data)), 'error'); return; }
    renderReqMessages(data.messages || []);
    updateReqStatus(data.status);
    input.focus();

    if (data.creative_brief && data.creative_brief.title) {
      showReqBrief(data.creative_brief);
      if (data.status !== 'brief_ready') document.getElementById('reqBriefArea').style.display = 'block';
    }
    if (data.production_plan) {
      showReqPlan(data.production_plan);
    } else if (data.status === 'plan_ready' || data.status === 'plan_confirmed') {
      fetchReqPlan();
    }
  }).catch(err => {
    input.disabled = false;
    showTyping(false);
    reqToast('发送失败: ' + err.message, 'error');
  });
}

// ── SSE 流式对话 ──────────────────────────────────

function reqSendStreamSSE(text) {
  // Fallback to non-streaming if SSE not available
  reqSendMessage();
}

// ── 文件上传 ──────────────────────────────────────

function reqUploadFile() {
  const fileInput = document.getElementById('reqFileInput');
  const file = fileInput?.files?.[0];
  if (!file || !reqSessionId) return;

  const formData = new FormData();
  formData.append('file', file);

  fetch(API_BASE() + '/api/requirements/upload/' + reqSessionId, {
    method: 'POST', body: formData,
  }).then(r => r.json()).then(data => {
    document.getElementById('reqFileName').textContent = '✅ ' + (file.name || '');
    reqToast('文件已上传参考', 'success');
    // 自动追加一条消息通知助手
    addUserMsg('我上传了参考文件: ' + file.name);
  }).catch(err => {
    reqToast('上传失败: ' + err.message, 'error');
  });
}

// ── 消息渲染 ──────────────────────────────────────

function renderReqMessages(messages) {
  const msgArea = document.getElementById('reqMessages');
  if (!messages || messages.length === 0) return;

  // 清除已有消息（保留 typing indicator）
  const typingEl = document.getElementById('reqTypingIndicator');
  msgArea.innerHTML = '';
  if (typingEl) msgArea.appendChild(typingEl);

  for (const msg of messages) {
    if (!msg || !msg.role || !msg.content) continue;
    addMsgBubble(msg.role, msg.content, msg.metadata);
  }
  scrollToBottom();
}

function addMsgBubble(role, content, metadata) {
  const msgArea = document.getElementById('reqMessages');
  const isUser = role === 'user';
  const wrapper = document.createElement('div');
  wrapper.className = 'chat-msg ' + (isUser ? 'user' : 'ai') + ' req-msg';

  if (isUser) {
    wrapper.textContent = content;
  } else {
    const md = document.createElement('div');
    md.className = 'md-content';
    md.innerHTML = renderMD(content);
    wrapper.appendChild(md);
  }

  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = isUser ? '你' : '需求助手';
  wrapper.appendChild(label);
  // 插入到 typing indicator 前面
  const typingEl = document.getElementById('reqTypingIndicator');
  if (typingEl) msgArea.insertBefore(wrapper, typingEl);
  else msgArea.appendChild(wrapper);
}

function addUserMsg(text) {
  addMsgBubble('user', text);
  scrollToBottom();
}

function scrollToBottom() {
  const msgArea = document.getElementById('reqMessages');
  if (msgArea) msgArea.scrollTop = msgArea.scrollHeight;
}

// ── Typing Indicator ──────────────────────────────

function showTyping(show) {
  const el = document.getElementById('reqTypingIndicator');
  if (!el) return;
  el.style.display = show ? 'flex' : 'none';
  if (show) scrollToBottom();
}

// ── 创作方案 ──────────────────────────────────────

function showReqBrief(brief) {
  const area = document.getElementById('reqBriefArea');
  const content = document.getElementById('reqBriefContent');
  area.style.display = 'block';
  if (!brief || !brief.title) { content.innerHTML = '<p class="text-secondary">方案正在生成...</p>'; return; }

  content.innerHTML = `
    <div class="brief-field"><strong>标题</strong>: ${esc(brief.title || '待定')}</div>
    <div class="brief-field"><strong>概述</strong>: ${esc(brief.overview || '')}</div>
    <div class="brief-field"><strong>目标受众</strong>: ${esc(brief.target_audience || '')}</div>
    <div class="brief-field"><strong>核心信息</strong>: ${esc(brief.core_message || '')}</div>
    <div class="brief-field"><strong>风格方向</strong>: ${esc(brief.style_direction || '')}</div>
    <div class="brief-field"><strong>结构建议</strong>: ${esc(brief.structure_suggestion || '')}</div>
    <div class="brief-field"><strong>预估时长</strong>: ${esc(brief.duration_estimate || '')}</div>
    ${brief.key_elements ? '<div class="brief-field"><strong>关键元素</strong>: ' + brief.key_elements.map(e => '<span class="tag">' + esc(e) + '</span>').join(' ') + '</div>' : ''}
    ${brief.special_requirements ? '<div class="brief-field"><strong>特殊要求</strong>: ' + brief.special_requirements.map(e => '<span class="tag tag-warning">' + esc(e) + '</span>').join(' ') + '</div>' : ''}
  `;
}

function reqConfirmBrief() {
  if (!reqSessionId) return;
  showTyping(true);
  api('POST', '/api/requirements/chat', {
    session_id: reqSessionId, message: '确认',
  }).then(({ok, data}) => {
    showTyping(false);
    if (!ok) { reqToast('确认失败', 'error'); return; }
    document.getElementById('reqBriefArea').style.display = 'none';
    renderReqMessages(data.messages || []);
    updateReqStatus(data.status);
    if (data.status === 'plan_ready') fetchReqPlan();
    else pollReqPlan();
  }).catch(err => { showTyping(false); reqToast('确认失败: ' + err.message, 'error'); });
}

// ── 规划书 ────────────────────────────────────────

function pollReqPlan() {
  if (reqPollTimer) clearInterval(reqPollTimer);
  reqPollTimer = setInterval(() => {
    if (!reqSessionId) { clearInterval(reqPollTimer); return; }
    fetchReqPlan();
  }, 2000);
}

function fetchReqPlan() {
  if (!reqSessionId) return;
  api('GET', '/api/requirements/plan/' + reqSessionId).then(({ok, data}) => {
    if (!ok) return;
    showReqPlan(data);
    if (reqPollTimer) { clearInterval(reqPollTimer); reqPollTimer = null; }
    updateReqStatus('plan_ready');
  }).catch(() => {});
}

function showReqPlan(plan) {
  const area = document.getElementById('reqPlanArea');
  const content = document.getElementById('reqPlanContent');
  const nav = document.getElementById('reqPlanNav');
  const stats = document.getElementById('reqPlanStats');
  area.style.display = 'block';

  if (!plan || !plan.markdown) {
    content.innerHTML = '<p class="text-secondary">⏳ 规划书正在生成中...</p>';
    nav.innerHTML = '';
    return;
  }

  // 渲染 Markdown 内容
  content.innerHTML = renderMD(plan.markdown);
  stats.textContent = `${plan.scene_count || 0} 个场景 · 约 ${Math.round((plan.total_duration_sec || 0) / 60)} 分钟`;

  // 生成标题导航
  const headings = content.querySelectorAll('h1, h2, h3');
  if (headings.length > 0) {
    let navHtml = '<div style="padding:8px 0;font-weight:bold;color:var(--accent);font-size:13px">📑 目录</div>';
    headings.forEach(h => {
      const indent = h.tagName === 'H1' ? '0' : h.tagName === 'H2' ? '12px' : '24px';
      const id = 'h-' + Math.random().toString(36).slice(2);
      h.id = id;
      navHtml += `<div style="padding:4px 0 4px ${indent};font-size:12px;cursor:pointer;color:var(--text2)" onclick="document.getElementById('${id}').scrollIntoView({behavior:'smooth'})">${h.textContent}</div>`;
    });
    nav.innerHTML = navHtml;
  }

  document.getElementById('reqConfirmPlanBtn').disabled = false;
}

function reqConfirmPlan() {
  if (!reqSessionId) return;
  const btn = document.getElementById('reqConfirmPlanBtn');
  btn.disabled = true;
  btn.textContent = '⏳ 正在启动...';

  // 把需求工作台的参数同步到剪辑工作台
  const topic = document.getElementById('reqTopic').value.trim();
  const script = document.getElementById('reqScript').value.trim();
  const personaId = document.getElementById('reqPersona').value.trim();
  const pluginId = document.getElementById('reqCategory').value;
  const audioPath = document.getElementById('reqAudioPath').value.trim();
  const dur = parseFloat(document.getElementById('reqDuration').value) || 300;

  const ewTopic = document.getElementById('ewTopic');
  const ewScript = document.getElementById('ewScript');
  const ewPersona = document.getElementById('ewPersonaId');
  const ewPlugin = document.getElementById('ewPluginId');
  const ewDuration = document.getElementById('ewDuration');
  const ewAudio = document.getElementById('ewAudioPath');
  if (ewTopic) ewTopic.value = topic;
  if (ewScript) ewScript.value = script;
  if (ewPersona) ewPersona.value = personaId || 'zam_knowledge_critical';
  if (ewPlugin) ewPlugin.value = pluginId;
  if (ewDuration) ewDuration.value = dur;
  if (ewAudio && audioPath) ewAudio.value = audioPath;

  // 切换到剪辑工作台，用剪辑工作台的管线
  document.getElementById('reqPlanArea').style.display = 'none';
  document.getElementById('reqChatArea').style.display = 'none';
  document.getElementById('reqInitForm').style.display = 'block';
  switchSection('edit-workspace');
  setTimeout(() => {
    ewLog('需求已确认，启动视频制作管线...', 'title');
    runAiEdit();
  }, 500);
}

function reqRequestModify() {
  document.getElementById('reqPlanArea').style.display = 'none';
  document.getElementById('reqChatArea').style.display = 'block';
  document.getElementById('reqInput').focus();
}

function reqExportPlan() {
  api('GET', '/api/requirements/plan/' + reqSessionId).then(({ok, data}) => {
    if (!ok || !data || !data.markdown) return;
    const blob = new Blob([data.markdown], {type: 'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = '规划书_' + reqSessionId.slice(-8) + '.md';
    a.click();
    URL.revokeObjectURL(url);
  });
}

// ── 管线进度 ──────────────────────────────────────

function pollReqPipeline() {
  const fill = document.getElementById('reqProgressFill');
  const text = document.getElementById('reqProgressText');
  let progress = 10;

  const interval = setInterval(() => {
    if (!reqSessionId) { clearInterval(interval); return; }
    api('GET', '/api/requirements/session/' + reqSessionId).then(({ok, data}) => {
      if (!ok) return;
      const statusArea = document.getElementById('reqPipelineStatus');
      if (data.status === 'pipeline_done' || data.status === 'completed') {
        clearInterval(interval);
        if (fill) fill.style.width = '100%';
        if (text) text.textContent = '✅ 视频制作完成！';
        statusArea.innerHTML +=
          '<p class="text-success" style="margin-top:16px">✅ 视频制作完成！</p>' +
          '<p>请前往"剪辑工作台"查看和编辑。</p>';
      } else if (data.status === 'error') {
        clearInterval(interval);
        if (text) text.textContent = '❌ 制作过程出现错误';
        statusArea.innerHTML += '<p class="text-danger">❌ 制作失败，请重试。</p>';
      } else {
        progress = Math.min(progress + 5, 90);
        if (fill) fill.style.width = progress + '%';
        if (text) text.textContent = '⏳ 制作中...';
      }
    }).catch(() => {});
  }, 3000);
}

// ── 会话刷新/结束 ──────────────────────────────

function reqRefreshSession() {
  if (!reqSessionId) return;
  showTyping(true);
  api('GET', '/api/requirements/session/' + reqSessionId).then(({ok, data}) => {
    showTyping(false);
    if (!ok) return;
    renderReqMessages(data.messages || []);
    updateReqStatus(data.status);
    if (data.creative_brief && data.brief === 'brief_ready') showReqBrief(data.creative_brief);
    if (data.production_plan) showReqPlan(data.production_plan);
  }).catch(() => showTyping(false));
}

function reqConfirmEnd() {
  if (confirm('确定结束当前会话？')) reqResetSession();
}

function reqResetSession() {
  reqSessionId = '';
  clearDraft();
  document.getElementById('reqChatArea').style.display = 'none';
  document.getElementById('reqBriefArea').style.display = 'none';
  document.getElementById('reqPlanArea').style.display = 'none';
  document.getElementById('reqPipelineArea').style.display = 'none';
  document.getElementById('reqInitForm').style.display = 'block';
  document.getElementById('reqMessages').innerHTML = '';
  ['reqBriefContent','reqPlanContent','reqPlanNav'].forEach(id => {
    const el = document.getElementById(id); if (el) el.innerHTML = '';
  });
  if (reqPollTimer) { clearInterval(reqPollTimer); reqPollTimer = null; }
  reqToast('会话已结束', 'info');
}

// ── 状态更新 ──────────────────────────────────────

function updateReqStatus(status) {
  const badge = document.getElementById('reqStatusBadge');
  if (!badge) return;
  const labels = {
    gathering: '💬 需求收集', brief_ready: '📝 方案待确认',
    brief_confirmed: '✅ 方案已确认', planning: '⏳ 生成规划书',
    plan_ready: '📋 规划书待确认', plan_confirmed: '✅ 规划书已确认',
    pipeline_running: '⚙️ 制作中', pipeline_done: '✅ 已完成',
    error: '❌ 错误', cancelled: '🗑️ 已取消',
  };
  badge.textContent = labels[status] || status;
}

// ── 恢复未完成会话 ──────────────────────────────

(function checkDraft() {
  const draft = loadDraft();
  if (draft && draft.session_id) {
    const btn = document.createElement('div');
    btn.style.cssText = 'margin-top:12px;padding:12px;background:var(--surface2);border-radius:8px;border:1px solid var(--accent);cursor:pointer';
    btn.innerHTML = '📂 检测到未完成的会话，<strong>点击恢复</strong>';
    btn.onclick = () => {
      reqSessionId = draft.session_id;
      document.getElementById('reqInitForm').style.display = 'none';
      document.getElementById('reqChatArea').style.display = 'block';
      showTyping(true);
      api('GET', '/api/requirements/session/' + reqSessionId).then(({ok, data}) => {
        showTyping(false);
        if (!ok) { reqToast('会话已过期', 'error'); reqResetSession(); return; }
        renderReqMessages(data.messages || []);
        updateReqStatus(data.status);
        if (data.creative_brief && (data.status === 'brief_ready' || data.status === 'brief_confirmed')) showReqBrief(data.creative_brief);
        if (data.production_plan) showReqPlan(data.production_plan);
        if (data.status === 'plan_ready' || data.status === 'plan_confirmed') fetchReqPlan();
      }).catch(() => { showTyping(false); });
      btn.remove();
    };
    const form = document.getElementById('reqInitForm');
    if (form) form.appendChild(btn);
  }
})();

function esc(text) {
  if (!text) return '';
  const d = document.createElement('div'); d.textContent = text; return d.innerHTML;
}

// ═══════════════════════════════════════════════════════════════
// 配音上传 + STT 对齐 + 字幕切分 (与剪辑工作台相同)
// ═══════════════════════════════════════════════════════════════

let _reqSttSegments = [];
let _reqCaptionSegments = [];

async function reqUploadAudio(e) {
  const input = e.target;
  const file = input?.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch(API_BASE() + "/api/asset/upload", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || d.error || "HTTP " + r.status);
    if (!d.file_path) throw new Error("no file_path");
    document.getElementById("reqAudioPath").value = d.file_path;
    if (d.duration_sec > 0) {
      document.getElementById("reqDuration").value = Math.ceil(d.duration_sec);
      document.getElementById("reqAudioDuration").value = d.duration_sec;
    }
    reqToast("配音上传成功: " + (d.duration_sec?.toFixed(0) || "") + "s", "success");
  } catch(err) {
    reqToast("上传失败: " + err.message, "error");
  }
}

function reqSplitScript() {
  const text = document.getElementById("reqScript").value.trim();
  const mode = document.getElementById("reqSplitMode")?.value || "period";
  if (!text) return [];
  if (mode === "punctuation") {
    const parts = [];
    let buf = "";
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if ("，。；？！".includes(ch)) {
        if ("？！".includes(ch)) { parts.push((buf + ch).trim()); }
        else { if (buf.trim()) parts.push(buf.trim()); }
        buf = "";
      } else { buf += ch; }
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts.filter(p => p);
  }
  return text.split(/[。！？.!?]/).map(s => s.trim()).filter(s => s);
}

async function reqAlignStt(audioPath, segments) {
  const alignedText = segments.join("\n");
  try {
    const r = await fetch(API_BASE() + "/api/stt/align", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_path: audioPath, transcript_text: alignedText }),
    });
    const d = await r.json();
    if (d.segments && d.segments.length > 0) {
      _reqSttSegments = d.segments;
      const last = d.segments[d.segments.length - 1];
      return last?.end || 0;
    }
  } catch(e) { console.error("STT align failed", e); }
  return 0;
}
