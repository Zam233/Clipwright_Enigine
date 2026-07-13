/* Chat Forge 模块 */
let chatSessionId = null, _lastProgress = {};
const KB_CHUNK_LIMIT = 6000;

function addChatMessage(role, content) {
  const container = document.getElementById('chatMessages');
  const welcome = container.querySelector('.chat-welcome');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  div.textContent = content;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function addChatSystem(msg) {
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'chat-msg system';
  div.textContent = msg;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function updateChatPreview(draft) {
  if (!draft) return;
  const el = document.getElementById('chatPreview');
  let html = '';
  const sections = { identity: '身份', language: '语言', rhythm: '节奏', visual: '视觉', audio: '音频', constraints: '约束' };
  for (const [key, label] of Object.entries(sections)) {
    const val = draft[key];
    if (!val) continue;
    html += `<div class="field"><span class="key">${label}:</span> `;
    if (typeof val === 'object') {
      const parts = Object.entries(val).filter(([_, v]) => v && v !== '').map(([k, v]) => Array.isArray(v) ? `${k}=[${v.join(',')}]` : `${k}=${v}`);
      html += `<span class="val">${parts.join(', ')}</span>`;
    } else { html += `<span class="val">${val}</span>`; }
    html += '</div>';
  }
  el.innerHTML = html || '<pre>等待数据...</pre>';
}
function updateChatProgress(progress) {
  if (!progress) return;
  const merged = { ..._lastProgress, ...progress };
  _lastProgress = merged;
  const dims = ['identity','language','rhythm','visual','audio','constraints'];
  let total = 0, count = 0;
  for (const d of dims) { const v = merged[d]; if (typeof v === 'number' && v >= 0) { total += v; count++; } }
  const avg = count > 0 ? total / count : 0, overallPct = Math.round(avg * 100);
  for (const d of dims) {
    const val = merged[d] ?? 0, pct = Math.round(val * 100);
    const pEl = document.getElementById('p_' + d), pbEl = document.getElementById('pb_' + d);
    if (pEl) pEl.textContent = pct + '%';
    if (pbEl) pbEl.style.width = pct + '%';
  }
  document.getElementById('chatProgressPct').textContent = overallPct;
  document.getElementById('chatOverallBar').style.width = overallPct + '%';
  const badge = document.getElementById('chatReadyBadge');
  if (badge) badge.style.display = overallPct >= 70 ? 'inline' : 'none';
}
function showChatInterface() {
  document.getElementById('chatInputArea').style.display = 'flex';
  document.getElementById('chatOverallProgress').style.display = 'block';
  document.getElementById('chatSidebar').style.display = 'flex';
  document.getElementById('chatInput').focus();
}
function showLoading(show) { const el = document.getElementById('chatLoading'); if (el) el.style.display = show ? 'flex' : 'none'; }
function showKnowledgeProgress(fileName, total) {
  document.getElementById('kbFileName').textContent = `📖 ${fileName}`;
  document.getElementById('kbProgressNum').textContent = '0';
  document.getElementById('kbProgressTotal').textContent = total;
  document.getElementById('chatKbBar').style.width = '0%';
  document.getElementById('chatKbBar').className = 'fill kb-pulse';
  document.getElementById('kbProgressLabel').textContent = total > 1 ? `正在逐段分析，共 ${total} 章...` : '正在分析...';
  document.getElementById('chatKbProgress').style.display = 'block';
}
function updateKnowledgeProgress(current, total, label) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  document.getElementById('kbProgressNum').textContent = Math.min(current, total);
  document.getElementById('chatKbBar').style.width = pct + '%';
  document.getElementById('kbProgressLabel').textContent = label || `第 ${current}/${total} 章`;
}
function hideKnowledgeProgress(success) {
  const el = document.getElementById('chatKbProgress');
  document.getElementById('chatKbBar').className = 'fill';
  if (success) {
    document.getElementById('kbProgressLabel').textContent = `已完成 ${document.getElementById('kbProgressTotal').textContent} 章分析`;
    document.getElementById('chatKbBar').style.width = '100%';
    setTimeout(() => { el.style.display = 'none'; }, 3000);
  } else { el.style.display = 'none'; }
}
function splitByH1(text, maxLen) {
  if (text.length <= maxLen) return [{ heading: '', content: text }];
  const sections = text.split(/^# /m);
  const chunks = [];
  for (const section of sections) {
    if (!section.trim()) continue;
    const lines = section.split('\n'), heading = lines[0].trim(), body = lines.slice(1).join('\n').trim();
    const chunkContent = heading ? `# ${heading}\n\n${body}` : body;
    chunks.push({ heading: heading || `section_${chunks.length+1}`, content: chunkContent.length > maxLen ? chunkContent.slice(0, maxLen) : chunkContent });
  }
  return chunks.length ? chunks : [{ heading: '', content: text.slice(0, maxLen) }];
}
async function chatStart() {
  try {
    showLoading(true);
    const { ok, data } = await api('POST', '/api/persona/forge/chat/start', { persona_id: '' });
    showLoading(false);
    if (!ok) { addChatSystem('启动失败'); return; }
    chatSessionId = data.session_id;
    showChatInterface();
    addChatMessage('ai', '你好！我是帧艺 ClipWright 的创作风格顾问。\n\n你可以用日常语言描述你的创作风格，例如：\n• "我做数码评测，毒舌吐槽，画面高对比暗色调，节奏偏快"\n• "知识区长视频，偏学术，画面简洁，留白多，BGM用电子工业风"\n• "Vlog 日常，轻松温暖，画面明亮，节奏舒缓"\n\n也可以上传参考脚本文档（📎按钮），我会分析你的语言习惯。\n随时说"改一下"来调整，满意后点"保存"就行。');
    updateChatPreview(data.persona_draft);
    updateChatProgress(data.progress);
  } catch(e) { showLoading(false); addChatSystem('错误: ' + e.message); }
}
async function chatSend() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || !chatSessionId) return;
  input.value = ''; input.style.height = 'auto';
  addChatMessage('user', text);
  showLoading(true);
  try {
    const { ok, data } = await api('POST', '/api/persona/forge/chat/message', { session_id: chatSessionId, message: text }, 120000);
    showLoading(false);
    if (!ok) { addChatMessage('ai', '抱歉，出了点问题。'); return; }
    addChatMessage('ai', data.reply);
    updateChatPreview(data.persona_draft);
    updateChatProgress(data.progress);
  } catch(e) { showLoading(false); addChatMessage('ai', '通信错误: ' + e.message); }
}
async function chatUploadKnowledge(event) {
  const file = event.target.files[0];
  if (!file || !chatSessionId) return;
  const text = await file.text();
  const sections = splitByH1(text, KB_CHUNK_LIMIT);
  const total = sections.length;
  addChatSystem(`正在分析参考文档: ${file.name} (${text.length} 字, ${total} 段)`);
  showKnowledgeProgress(file.name, total);
  let lastReply = null;
  try {
    for (let i = 0; i < total; i++) {
      const sec = sections[i];
      updateKnowledgeProgress(i+1, total, `第 ${i+1}/${total} 章：${(sec.heading||'').slice(0,30)}`);
      const { ok, data } = await api('POST', '/api/persona/forge/chat/knowledge', { session_id: chatSessionId, content: sec.content, source: file.name });
      if (!ok) { hideKnowledgeProgress(false); addChatSystem(`第 ${i+1} 段分析失败`); event.target.value = ''; return; }
      if (data.reply) lastReply = data.reply;
      if (data.persona_draft) updateChatPreview(data.persona_draft);
      if (data.progress) updateChatProgress(data.progress);
    }
    hideKnowledgeProgress(true);
    const kbList = document.getElementById('kbList');
    const empty = kbList.querySelector('.text-muted');
    if (empty) empty.remove();
    if (!kbList.querySelector(`.knowledge-item[data-source="${file.name}"]`)) {
      const item = document.createElement('div');
      item.className = 'knowledge-item'; item.dataset.source = file.name;
      item.innerHTML = `<span class="name">📄 ${file.name}</span><span class="badge">${total > 1 ? total+' 章' : text.length+' 字'}</span>`;
      kbList.appendChild(item);
    }
    document.getElementById('kbCount').textContent = `(${kbList.children.length})`;
    if (lastReply && total > 1) addChatMessage('ai', lastReply);
  } catch(e) { hideKnowledgeProgress(false); addChatSystem('上传错误: ' + e.message); }
  event.target.value = '';
}
async function chatCommit() {
  if (!chatSessionId) return;
  const id = prompt('Persona ID:', 'chat_persona_' + Date.now().toString(36));
  if (!id) return;
  const name = prompt('Persona 名称:', '对话创建_' + id.slice(-8));
  if (!name) return;
  addChatSystem('正在保存 Persona...');
  try {
    const { ok, data } = await api('POST', '/api/persona/forge/chat/commit', { session_id: chatSessionId, persona_id: id, persona_name: name });
    if (!ok) { addChatSystem('保存失败: ' + JSON.stringify(data)); return; }
    const summary = `Persona 已保存\n\nID: ${data.persona_id}\n语调: ${(data.parameter||{}).identity?.tone || '未设置'}\n节奏: ${(data.parameter||{}).rhythm?.cut_profile || '默认'}\n时长上限: ${(data.parameter||{}).constraints?.max_duration_sec || 900}s`;
    addChatSystem(summary);
    const preview = document.getElementById('chatPreview');
    if (preview) preview.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
    ['identity','language','rhythm','visual','audio','constraints'].forEach(d => {
      const p = document.getElementById('p_' + d), pb = document.getElementById('pb_' + d);
      if (p) p.textContent = '100%';
      if (pb) pb.style.width = '100%';
    });
  } catch(e) { addChatSystem('保存错误: ' + e.message); }
}
function chatReset() {
  chatSessionId = null; _lastProgress = {};
  document.getElementById('chatMessages').innerHTML = `
    <div class="chat-welcome"><h2>对话创建 Persona</h2><p>和 AI 创作顾问自然对话。<br>可以随时上传参考文档。</p><button class="btn btn-primary" onclick="chatStart()">开始对话</button></div>`;
  document.getElementById('chatInputArea').style.display = 'none';
  document.getElementById('chatOverallProgress').style.display = 'none';
  document.getElementById('chatLoading').style.display = 'none';
  document.getElementById('chatKbProgress').style.display = 'none';
  document.getElementById('chatSidebar').style.display = 'none';
  const preview = document.getElementById('chatPreview');
  if (preview) preview.innerHTML = '<pre>等待对话开始...</pre>';
  ['identity','language','rhythm','visual','audio','constraints'].forEach(d => {
    const p = document.getElementById('p_' + d), pb = document.getElementById('pb_' + d);
    if (p) p.textContent = '0%';
    if (pb) pb.style.width = '0%';
  });
}
