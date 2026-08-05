/* 对话式编辑 */
let _edSessionId = null;
let _edHistory = [];

async function edNewSession() {
  try {
    const r = await api('POST', '/api/edit/session/create');
    _edSessionId = r.data?.session_id;
    document.getElementById('edSessionInfo').innerHTML =
      `<div style="font-size:11px"><span style="color:var(--green)">●</span> 会话: ${_edSessionId}</div>
       <div style="font-size:10px;color:var(--text2);margin-top:4px">输入指令开始编辑</div>`;
    document.getElementById('edMessages').innerHTML = '';
    edAddMsg('system', '编辑会话已创建，请输入编辑指令。');
  } catch(ex) {
    alert('创建会话失败: ' + ex.message);
  }
}

async function edSend() {
  const input = document.getElementById('edInput');
  const msg = input.value.trim();
  if (!msg || !_edSessionId) { alert('请先创建会话'); return; }
  input.value = '';
  edAddMsg('user', msg);

  try {
    const r = await fetch(API_BASE() + '/api/edit/session/' + _edSessionId + '/chat?message=' + encodeURIComponent(msg));
    const d = await r.json();
    const reply = d.llm_reply || (d.success ? '操作成功' : '操作失败: ' + (d.error || ''));
    edAddMsg('assistant', reply);
    if (d.output) {
      edAddMsg('system', '执行结果: ' + JSON.stringify(d.output).slice(0, 200));
    }
  } catch(ex) {
    edAddMsg('assistant', '请求失败: ' + ex.message);
  }
}

function edAddMsg(role, text) {
  const el = document.getElementById('edMessages');
  const colors = { user: 'var(--accent)', assistant: 'var(--green)', system: 'var(--text2)' };
  const labels = { user: '你', assistant: '编辑助手', system: '系统' };
  el.innerHTML += `<div style="margin-bottom:8px;padding:6px 10px;border-radius:6px;background:var(--surface2)">
    <div style="font-size:9px;color:${colors[role]||'var(--text2)'};margin-bottom:2px">${labels[role]||role}</div>
    <div style="font-size:12px">${text}</div>
  </div>`;
  el.scrollTop = el.scrollHeight;
}
