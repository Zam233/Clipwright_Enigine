/* 模型测试模块 */
function switchModelTab(name, el) {
  document.querySelectorAll('#section-model-test .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('#section-model-test .card-body[id^="model-"]').forEach(d => d.style.display = 'none');
  document.getElementById('model-' + name).style.display = 'block';
  if (name === 'info') loadModelConfig();
}
async function loadModelConfig() {
  const { ok, data } = await api('GET', '/api/test/config');
  const el = document.getElementById('modelConfigInfo');
  if (el) el.textContent = ok ? JSON.stringify(data, null, 2) : '加载失败';
}
async function testLlm() {
  const el = document.getElementById('modelLlmResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">发送中...</span></div>';
  const { ok, data } = await api('POST', '/api/test/llm', { prompt: document.getElementById('modelLlmPrompt').value, model: document.getElementById('modelLlmModel').value });
  if (!ok) { el.innerHTML = '<div class="tag tag-error">请求失败</div>'; return; }
  if (!data.success) { el.innerHTML = `<div class="result-box"><pre>错误: ${data.error}</pre></div>`; return; }
  el.innerHTML = `<div class="result-box"><div class="result-header"><span>${data.provider}/${data.model}</span><span class="tag tag-success">${data.usage?.input_tokens||'?'} in / ${data.usage?.output_tokens||'?'} out</span></div><pre>${data.content}</pre></div>`;
}
async function testEmbed() {
  const el = document.getElementById('modelEmbedResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">生成中...</span></div>';
  const { ok, data } = await api('POST', '/api/test/embed', { text: document.getElementById('modelEmbedText').value, provider: document.getElementById('modelEmbedProvider').value });
  if (!ok) { el.innerHTML = '<div class="tag tag-error">请求失败</div>'; return; }
  if (!data.success) { el.innerHTML = `<div class="result-box"><pre>错误: ${data.error}</pre></div>`; return; }
  el.innerHTML = `<div class="result-box"><div class="result-header"><span>${data.provider}/${data.model}</span><span class="tag tag-success">${data.dimension} 维</span></div><pre>向量预览：\n[${(data.vector_preview||[]).join(', ')}]</pre></div>`;
}
async function testRerank() {
  const el = document.getElementById('modelRerankResult');
  el.innerHTML = '<div class="flex"><span class="spin"></span><span class="text-muted">重排序中...</span></div>';
  const candidates = document.getElementById('modelRerankCandidates').value.split('\n').filter(s => s.trim());
  const { ok, data } = await api('POST', '/api/test/rerank', { query: document.getElementById('modelRerankQuery').value, candidates, top_k: 5 });
  if (!ok) { el.innerHTML = '<div class="tag tag-error">请求失败</div>'; return; }
  if (!data.success) { el.innerHTML = `<div class="result-box"><pre>错误: ${data.error}</pre></div>`; return; }
  let html = `<div class="result-box"><div class="result-header"><span>${data.model}</span><span class="tag tag-success">${data.results.length} 条</span></div><pre>`;
  (data.results||[]).forEach((r, i) => { html += `${i+1}. [${r.score.toFixed(4)}] ${r.text}\n`; });
  el.innerHTML = html + '</pre></div>';
}
