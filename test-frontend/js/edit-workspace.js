/* 剪辑工作台 — AI 剪辑一体化工作流 */
let _lastTimelineData = null, _tlZoom = 1.0;

// ── 日志系统 ──
function ewLog(msg, type) {
  const panel = document.getElementById('ewLogPanel');
  const welcome = panel.querySelector('.text-muted');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.style.cssText = 'padding:2px 0;line-height:1.5';
  if (type === 'error') div.style.color = 'var(--red)';
  else if (type === 'success') div.style.color = 'var(--green)';
  else if (type === 'step') div.style.color = 'var(--accent)';
  else if (type === 'muted') div.style.color = 'var(--text2)';
  else div.style.color = 'var(--text)';
  div.style.fontSize = type === 'title' ? '12px' : '11px';
  div.style.fontWeight = type === 'title' ? '600' : '400';
  div.textContent = msg;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
}

function clearEwLog() {
  document.getElementById('ewLogPanel').innerHTML =
    '<div class="text-muted" style="text-align:center;padding:30px 0;font-size:11px;color:var(--text2)">等待操作...</div>';
}

function setEwProgress(pct, text) {
  const bar = document.getElementById('ewProgressBar');
  const label = document.getElementById('ewProgressText');
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = text;
  document.getElementById('ewProgress').style.display = 'block';
}

// ── 素材源加载 ──
async function loadEditSources() {
  const el = document.getElementById('ewSourceList');
  const { ok, data } = await api('GET', '/api/material/sources');
  if (!ok || !data || !data.length) {
    el.innerHTML = '<span class="text-muted" style="font-size:10px">无素材源 — 文字占位</span>';
    return;
  }
  el.innerHTML = data.map(s =>
    `<label style="display:inline-flex;align-items:center;gap:2px;padding:2px 6px;background:var(--surface2);border:1px solid var(--border);border-radius:3px;cursor:pointer;font-size:10px">
      <input type="checkbox" checked data-source-id="${s.id}" style="accent-color:var(--accent);width:10px;height:10px;margin:0">
      ${s.name}
    </label>`
  ).join('');
}

function getSelectedSources() {
  const checks = document.querySelectorAll('#ewSourceList input[type="checkbox"]:checked');
  return Array.from(checks).map(c => c.getAttribute('data-source-id'));
}

function importEditScript(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => { document.getElementById('ewScript').value = e.target.result; };
  reader.readAsText(file);
}

async function uploadAudio(event) {
  const file = event.target.files[0];
  if (!file) return;
  const el = document.getElementById('ewAudioPath');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch(API_BASE() + '/api/asset/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.file_path) {
      el.value = data.file_path;
      ewLog(`配音上传成功: ${data.filename}`, 'success');
    }
  } catch(e) {
    el.value = file.name;
    ewLog(`配音上传失败: ${e.message}`, 'error');
  }
}

// ── 主流程 ──
async function runAiEdit() {
  const btn = document.getElementById('ewRunBtn');
  btn.disabled = true; btn.textContent = '生成中...';
  clearEwLog();
  const startedAt = Date.now();

  const topic = document.getElementById('ewTopic').value.trim();
  const script = document.getElementById('ewScript').value.trim();
  const personaId = document.getElementById('ewPersonaId').value.trim();
  const pluginId = document.getElementById('ewPluginId').value;
  const audioPath = document.getElementById('ewAudioPath').value.trim();
  const selectedSources = getSelectedSources();

  ewLog(`=== AI 剪辑工作流 ===`, 'title');
  ewLog(`选题: ${topic}`, '');
  ewLog(`人格: ${personaId} | 类型: ${pluginId}`, 'muted');
  ewLog(`素材源: ${selectedSources.length ? selectedSources.join(', ') : '全部'}`, 'muted');
  if (audioPath) ewLog(`配音: ${audioPath}`, 'muted');

  try {
    // Step 1: STT
    setEwProgress(10, '正在对齐配音与文案...');
    ewLog(`[1/4] 对齐配音与文案...`, 'step');
    let sttSegments = [];
    if (audioPath && script) {
      const res = await fetch(API_BASE() + '/api/stt/align', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({audio_path: audioPath, transcript_text: script})
      });
      const data = await res.json();
      if (data.success) {
        sttSegments = data.segments || [];
        ewLog(`配音对齐完成: ${sttSegments.length} 个时间戳段落`, 'success');
      }
    }

    // Step 2: Pipeline
    setEwProgress(30, 'AI 管线运行中...');
    ewLog(`[2/4] AI 管线运行中...`, 'step');
    let pipelineResult;
    try {
      const res = await fetch(API_BASE() + '/api/pipeline/run', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          persona_id: personaId, category_plugin_id: pluginId, topic,
          extra_params: {
            material_source_ids: selectedSources,
            audio_duration_sec: sttSegments.length > 0 ? sttSegments[sttSegments.length-1].end : 0
          },
          dry_run: false
        })
      });
      pipelineResult = await res.json();
      if (!res.ok) throw new Error(pipelineResult?.detail || `HTTP ${res.status}`);
    } catch(e) {
      ewLog(`管线失败: ${e.message}`, 'error');
      btn.disabled = false; btn.textContent = '生成视频';
      return;
    }

    const tl = pipelineResult?.shared_data?.final_timeline;
    const steps = pipelineResult?.steps || [];
    const trace = pipelineResult?.shared_data?.execution_trace || [];

    // 显示步骤结果
    for (const s of steps) {
      const icon = s.status === 'completed' ? '✓' : s.status === 'failed' ? '✗' : '○';
      const dur = s.duration_ms ? ` (${(s.duration_ms/1000).toFixed(1)}s)` : '';
      const err = s.error ? ` → ${s.error.slice(0,120)}` : '';
      ewLog(`  ${icon} ${s.agent_name}: ${s.status}${dur}${err}`, s.status === 'failed' ? 'error' : 'muted');
    }

    // 显示追踪事件
    if (trace.length) {
      const typeIcons = {llm:'🤖', tool:'🔧', skill:'🧠', plugin:'🔌', agent_start:'▶', agent_end:'✓', error:'✗', info:'○'};
      for (const ev of trace.slice(-10)) { // 显示最近10条
        const icon = typeIcons[ev.type] || '·';
        ewLog(`  ${icon} ${ev.agent}: ${ev.summary}`, 'muted');
      }
    }

    if (!tl) {
      ewLog(`管线状态: ${pipelineResult?.status || 'unknown'}`, 'error');
      ewLog(`未生成时间线 — 检查 LLM API Key 或素材源配置`, 'error');
      btn.disabled = false; btn.textContent = '生成视频';
      return;
    }
    _lastTimelineData = tl;
    ewLog(`管线完成: ${steps.filter(s => s.status === 'completed').length}/6 Agent 通过`, 'success');

    // Step 3: Render
    setEwProgress(70, '正在渲染 MP4...');
    ewLog(`[3/4] 渲染视频...`, 'step');
    try {
      const res = await fetch(API_BASE() + '/api/render/start', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({timeline: tl, output_path: 'renders/ai_edit_output.mp4'})
      });
      const renderResult = await res.json();
      if (renderResult?.success) {
        ewLog(`渲染完成: ${renderResult.output_path} (${renderResult.duration_sec?.toFixed(1)}s)`, 'success');
        // Show preview
        const video = document.getElementById('ewPreviewVideo');
        const src = document.getElementById('ewPreviewSrc');
        const panel = document.getElementById('ewPreviewPanel');
        if (video && src) {
          // 从本地路径提取文件名，通过 /renders/ 静态路由访问
          const relPath = renderResult.output_path.replace(/\\/g, '/').split('/').pop();
          src.src = API_BASE() + '/renders/' + relPath;
          video.load();
          panel.style.display = 'block';
          ewLog('预览已加载，可在右侧播放', 'success');
        }
      } else {
        ewLog(`渲染: ${renderResult?.error || '无输出'}`, 'muted');
      }
    } catch(e) {
      ewLog(`渲染失败: ${e.message}`, 'error');
    }

    // Step 4: Show timeline
    setEwProgress(100, '完成');
    ewLog(`[4/4] 完成 (${((Date.now()-startedAt)/1000).toFixed(1)}s)`, 'success');
    renderTimelineWithCaptions(tl, sttSegments);

  } catch(e) {
    ewLog(`错误: ${e.message}`, 'error');
  }

  btn.disabled = false; btn.textContent = '生成视频';
}

// ── Timeline Renderer ──
function renderTimelineWithCaptions(tl, sttSegments) {
  if (!tl) return;
  const container = document.getElementById('timelineInner');
  const info = document.getElementById('tlInfo');
  const dur = tl.duration_sec || 60;
  info.textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${dur.toFixed(1)}s`;

  const zoom = _tlZoom, pxPerSec = 100 * zoom;
  const totalWidth = Math.max(dur * pxPerSec, 400);
  const trackH = 48, rulerH = 26, labelW = 70;

  let html = `<div style="display:flex;flex-direction:column;min-width:${labelW+totalWidth+10}px;user-select:none">`;
  // Ruler
  html += `<div style="display:flex;height:${rulerH}px;position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border)">`;
  html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);font-size:9px;color:var(--text2);display:flex;align-items:center;justify-content:center">时间</div>`;
  html += `<div style="flex:1;position:relative;overflow:hidden">`;
  const tickInterval = Math.max(1, Math.floor(5 / zoom));
  for (let t = 0; t <= dur; t += tickInterval/2) {
    const x = t * pxPerSec;
    const isMain = t % tickInterval === 0;
    html += `<div style="position:absolute;left:${x}px;bottom:0;width:1px;height:${isMain?10:5}px;background:var(--border)"></div>`;
    if (isMain) html += `<div style="position:absolute;left:${x}px;bottom:12px;transform:translateX(-50%);font-size:9px;color:var(--text2)">${t.toFixed(1)}s</div>`;
  }
  html += `</div></div>`;

  // Tracks
  const colors = {video:'#4f8cff', audio:'#34d399', text:'#fbbf24', caption:'#f59e0b', image:'#a855f7'};
  const trackNames = {video:'视频', audio:'音频', text:'文字', caption:'字幕', image:'图片'};

  for (const track of (tl.tracks||[])) {
    const kind = track.kind || 'video';
    const color = colors[kind] || '#888';
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">${trackNames[kind]||kind}</div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;
    for (let t = 0; t <= dur; t += tickInterval/2) {
      html += `<div style="position:absolute;left:${t*pxPerSec}px;top:0;width:1px;height:100%;background:rgba(255,255,255,0.02)"></div>`;
    }
    for (const clip of (track.clips||[])) {
      const x = (clip.start_sec||0) * pxPerSec;
      const w = Math.max((clip.duration_sec||1) * pxPerSec, 4);
      const meta = clip.metadata || {};
      const label = clip.text || meta.label || meta.source_title || (clip.asset_id ? clip.asset_id.split('/').pop().split('\\').pop().slice(0,20) : '') || clip.id || '';
      const title = `${label} | ${clip.start_sec.toFixed(1)}s-${(clip.start_sec+clip.duration_sec).toFixed(1)}s`;
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;background:${color}44;border:1px solid ${color}88;display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;font-size:10px" title="${title}">`;
      if (w > 30) html += `<span style="color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label.slice(0,15)}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }

  // STT track
  if (sttSegments && sttSegments.length > 0) {
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">配音</div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;
    for (const seg of sttSegments) {
      const x = (seg.start||0) * pxPerSec;
      const w = Math.max(((seg.end||seg.start+1)-seg.start) * pxPerSec, 6);
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;background:#fbbf2444;border:1px solid #fbbf2488;display:flex;align-items:center;justify-content:center;overflow:hidden;font-size:10px;cursor:pointer" title="${seg.text}">`;
      if (w > 40) html += `<span style="color:#fff;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${seg.text}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }

  html += '</div>';
  container.innerHTML = html;
  document.getElementById('tlRange').textContent = `${dur.toFixed(1)}s`;
}

function zoomTimeline(delta) {
  _tlZoom = Math.max(0.3, Math.min(5, _tlZoom + delta));
  if (_lastTimelineData) renderTimelineWithCaptions(_lastTimelineData, []);
}
