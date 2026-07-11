/* Persona 模块 */
async function listPersonas() {
  const { ok, data } = await api('GET', '/api/persona/list');
  showResult('personaList', data, ok);
}

async function getPersona() {
  const id = document.getElementById('personaGetId').value;
  const { ok, data } = await api('GET', `/api/persona/${id}`);
  if (!ok) { showResult('personaDetail', data, false); return; }
  const yamlText = data.parameter ? JSON.stringify(data.parameter, null, 2) : '(无 parameter)';
  const promptText = data.prompt || '(无 prompt)';
  const kbList = data.knowledge || [];
  const html = `
    <div class="card" style="margin-top:12px;border-color:var(--accent)">
      <div class="card-header">
        <span>${data.persona_id} <span style="color:var(--text2);font-weight:400">v${data.version}</span></span>
        <span class="tag tag-success">已加载</span>
      </div>
      <div class="tabs" style="border-bottom:1px solid var(--border)">
        <div class="tab active" onclick="switchPersonaTab('yaml',this,'${id}')">YAML</div>
        <div class="tab" onclick="switchPersonaTab('prompt',this,'${id}')">Prompt</div>
        <div class="tab" onclick="switchPersonaTab('rag',this,'${id}')">RAG (${kbList.length})</div>
        <div class="tab" onclick="switchPersonaTab('query',this,'${id}')">检索</div>
      </div>
      <div id="personaTab-yaml" class="card-body">
        <pre style="font-size:12px;line-height:1.5;overflow-x:auto;white-space:pre-wrap">${yamlText}</pre>
      </div>
      <div id="personaTab-prompt" class="card-body" style="display:none">
        <pre style="font-size:12px;line-height:1.5;overflow-x:auto;white-space:pre-wrap">${promptText}</pre>
      </div>
      <div id="personaTab-rag" class="card-body" style="display:none">
        ${kbList.length === 0 ? '<div class="text-muted">无知识库文档</div>' : ''}
        ${kbList.map((doc, i) => `
          <div class="knowledge-item"><span class="name">${doc.title || doc.id || '未命名文档'}</span><span class="badge">${doc.content ? doc.content.length + ' 字' : '0 字'}</span></div>
          ${doc.content ? `<pre style="font-size:11px;line-height:1.4;color:var(--text2);padding:4px 12px 10px;margin:0;border-bottom:1px solid var(--border);white-space:pre-wrap">${doc.content.slice(0, 200)}${doc.content.length > 200 ? '...' : ''}</pre>` : ''}
        `).join('')}
        <div class="btn-group" style="margin-top:8px"><button class="btn btn-secondary btn-sm" onclick="indexRag('${id}')">建立向量索引</button></div>
      </div>
      <div id="personaTab-query" class="card-body" style="display:none">
        <div class="flex" style="gap:8px">
          <input id="ragQueryInput" placeholder="输入检索内容..." style="flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:var(--radius);font-size:13px">
          <button class="btn btn-primary btn-sm" onclick="queryRag('${id}')">检索</button>
        </div>
        <div id="ragQueryResult" style="margin-top:8px"></div>
      </div>
    </div>`;
  document.getElementById('personaDetail').innerHTML = html;
}

async function indexRag(personaId) {
  const { ok, data } = await api('POST', `/api/persona/${personaId}/rag/index`, { force_rebuild: true });
  const el = document.getElementById('ragQueryResult') || document.getElementById('personaTab-rag');
  const msg = ok ? `索引完成：${data.total_docs} 篇文档 → ${data.total_chunks} 个向量块` : `索引失败：${JSON.stringify(data)}`;
  if (el) el.innerHTML = `<div class="text-muted" style="margin-top:4px;font-size:12px">${msg}</div>`;
}

async function queryRag(personaId) {
  const q = document.getElementById('ragQueryInput').value.trim();
  if (!q) return;
  const el = document.getElementById('ragQueryResult');
  if (!el) return;
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">检索中...</span></div>';
  const { ok, data } = await api('POST', `/api/persona/${personaId}/rag/query`, { query: q, top_k: 5, rerank: true });
  if (!ok) { el.innerHTML = `<div class="tag tag-error">检索失败</div>`; return; }
  let html = `<div class="text-muted" style="font-size:12px;margin-bottom:6px">找到 ${data.total_chunks} 个相关片段</div>`;
  html += `<div style="font-size:11px;color:var(--text2);padding:8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:8px;line-height:1.5;white-space:pre-wrap">${data.context}</div>`;
  for (const c of (data.chunks || [])) {
    html += `<div class="knowledge-item"><span class="name">${c.source || c.id}</span><span class="badge">${c.score.toFixed(3)}</span></div>`;
    html += `<pre style="font-size:11px;line-height:1.4;color:var(--text2);padding:4px 12px 8px;margin:0;border-bottom:1px solid var(--border);white-space:pre-wrap">${c.text}</pre>`;
  }
  el.innerHTML = html;
}

function switchPersonaTab(tab, el, personaId) {
  document.querySelectorAll('#personaDetail .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  ['yaml', 'prompt', 'rag', 'query'].forEach(t => {
    const e = document.getElementById('personaTab-' + t);
    if (e) e.style.display = t === tab ? 'block' : 'none';
  });
  if (tab === 'rag') {
    api('GET', `/api/persona/${personaId}/knowledge`).then(({ data }) => {
      const c = document.getElementById('personaTab-rag');
      if (!c) return;
      if (!data || data.length === 0) { c.innerHTML = '<div class="text-muted">无知识库文档</div>'; return; }
      c.innerHTML = data.map(d => `
        <div class="knowledge-item"><span class="name">${d.title || d.id || '未命名'}</span><span class="badge">${(d.content || '').length} 字</span></div>
        <pre style="font-size:11px;line-height:1.4;color:var(--text2);padding:4px 12px 10px;margin:0;border-bottom:1px solid var(--border);white-space:pre-wrap">${(d.content || '').slice(0, 300)}</pre>
      `).join('');
    });
  }
}
