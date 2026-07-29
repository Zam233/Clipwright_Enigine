/* 剪辑工作台 — AI 剪辑一体化工作流 */
let _lastTimelineData = null, _tlZoom = 1.0, _lastSttSegments = [];
// 追踪管线完成后的状态: null | { finalTimeline, sttSegments }
let _pipelineResult = null;
// 预览状态
let _previewTime = 0, _previewPlaying = false, _previewTimer = null;
let _previewTl = null, _previewStt = null;

// ── 日志 ──
function ewLog(msg, type) {
  const panel = document.getElementById('ewLogPanel');
  if (!panel) { console.error('[Clipwright] ewLog: 日志面板未找到:', msg); return; }
  const welcome = panel.querySelector('.text-muted');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.style.cssText = 'padding:2px 0;line-height:1.5';
  const colors = {error:'var(--red)', success:'var(--green)', step:'var(--accent)', muted:'var(--text2)', title:'var(--accent)'};
  div.style.color = colors[type] || 'var(--text)';
  div.style.fontSize = type === 'title' ? '12px' : '11px';
  div.style.fontWeight = type === 'title' ? '600' : '400';
  div.textContent = msg;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
}
function clearEwLog() {
  const panel = document.getElementById('ewLogPanel');
  if (!panel) return;
  panel.innerHTML =
    '<div class="text-muted" style="text-align:center;padding:30px 0;font-size:11px;color:var(--text2)">等待操作...</div>';
}
function setEwProgress(pct, text) {
  const bar = document.getElementById('ewProgressBar');
  const label = document.getElementById('ewProgressText');
  const container = document.getElementById('ewProgress');
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = text;
  if (container) container.style.display = 'block';
}

async function loadEditSources() {
  const el = document.getElementById('ewSourceList');
  if (!el) { console.warn('loadEditSources: ewSourceList not found'); return; }
  el.innerHTML = '<span class="text-muted" style="font-size:10px;">加载中...</span>';
  try {
    const { ok, data } = await api('GET', '/api/material/sources');
    if (!ok || !data) { el.innerHTML = '<span class="text-muted" style="font-size:10px;color:var(--red)">API 错误</span>'; return; }
    const sources = Array.isArray(data) ? data : [];
    if (!sources.length) {
      el.innerHTML = '<span class="text-muted" style="font-size:10px">无素材源</span>'; return;
    }
    el.innerHTML = sources.map(s =>
      `<label style="display:inline-flex;align-items:center;gap:2px;padding:2px 6px;background:var(--surface2);border:1px solid var(--border);border-radius:3px;cursor:pointer;font-size:10px">
        <input type="checkbox" checked data-source-id="${s.id}" style="accent-color:var(--accent);width:10px;height:10px;margin:0"> ${s.name}
      </label>`
    ).join('');
  } catch(ex) {
    console.error('[Clipwright] 加载素材源失败:', ex);
    el.innerHTML = `<span class="text-muted" style="font-size:10px;color:var(--red)">加载失败: ${ex.message}</span>`;
  }
}
function getSelectedSources() {
  return Array.from(document.querySelectorAll('#ewSourceList input:checked')).map(c => c.getAttribute('data-source-id'));
}
function importEditScript(e) {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader(); r.onload = ev => { const el = document.getElementById('ewScript'); if (el) el.value = ev.target.result; }; r.readAsText(f);
}
async function uploadAudio(e) {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try {
    const r = await fetch(API_BASE() + '/api/asset/upload', { method:'POST', body:fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || d.error || `HTTP ${r.status}`);
    if (d.file_path) {
      const el = document.getElementById('ewAudioPath'); if (el) el.value = d.file_path;
      // 保存上传的音频时长供管线使用
      if (d.duration_sec > 0) {
        document.getElementById('ewAudioDuration').value = d.duration_sec;
        document.getElementById('ewDuration').value = Math.ceil(d.duration_sec);
        ewLog(`配音上传成功: ${d.duration_sec.toFixed(0)}s`, 'success');
      } else {
        ewLog('配音上传成功', 'success');
      }
    }
    else { throw new Error('服务器未返回 file_path'); }
  } catch(ex) {
    console.error('[Clipwright] 配音上传失败:', ex);
    ewLog(`上传失败: ${ex.message}`, 'error');
  }
}

// ── 文案拆分 ──
function splitScriptToCaptions(text, mode) {
  if (!text) return [];
  if (mode === 'punctuation') {
    // 按，。；？！拆分，保留？！“”在文本中
    const parts = [];
    let buf = '';
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if ('，。；？！'.includes(ch)) {
        if ('？！'.includes(ch)) {
          // 保留这些标点在当前段末尾
          parts.push((buf + ch).trim());
        } else {
          // 丢弃逗号句号分号
          if (buf.trim()) parts.push(buf.trim());
        }
        buf = '';
      } else {
        buf += ch;
      }
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts.filter(p => p);
  } else {
    // 按句号/感叹号/问号拆分
    const parts = text.split(/[。！？.!?]/).map(s => s.trim()).filter(s => s);
    return parts.length ? parts : [text];
  }
}

// ── 主流程：异步 + 实时时间线 ──
async function runAiEdit() {
  const btn = document.getElementById('ewRunBtn');
  if(btn){btn.disabled = true;btn.textContent = '运行中...';}
  clearEwLog();
  const startedAt = Date.now();
  const topic = document.getElementById('ewTopic').value.trim();
  const script = document.getElementById('ewScript').value.trim();
  const personaId = document.getElementById('ewPersonaId').value.trim();
  const pluginId = document.getElementById('ewPluginId').value;
  const audioPath = document.getElementById('ewAudioPath').value.trim();
  const selectedSources = getSelectedSources();
  const videoMode = document.getElementById('ewVideoMode')?.value || 'voiceover';
  const splitMode = document.getElementById('ewSplitMode')?.value || 'period';
  const orientation = document.getElementById('ewOrientation')?.value || 'landscape';
  const resolution = (document.getElementById('ewResolution')?.value || '1920x1080').split('x').map(Number);
  const renderFps = parseInt(document.getElementById('ewFps')?.value || '30');
  const renderBitrate = document.getElementById('ewBitrate')?.value || '5M';

  // 验证输入
  if (!topic && !script) {
    ewLog('错误: 请填写选题或文案', 'error');
    btn.disabled = false; btn.textContent = '生成视频';
    return;
  }

  ewLog('=== AI 剪辑工作流 ===', 'title');
  ewLog(`选题: ${topic}`, '');
  ewLog(`人格: ${personaId} | ${pluginId}`, 'muted');
  if (audioPath) ewLog(`配音: ${audioPath.split('/').pop()}`, 'muted');

  try {
    // Step 1: 字幕拆分 / 场景分析
    const isVisual = videoMode === 'visual';
    setEwProgress(10, isVisual ? '分析场景…' : '拆分字幕…');
    ewLog(`[1/4] ${isVisual ? '分析场景' : '拆分字幕'}…`, 'step');
    let sttSegments = [], audioDuration = 0, captionSegments = [];
    let scriptText = script;

    if (isVisual) {
      // 视觉模式：每行/每段是一个场景描述
      const lines = script.split('\n').map(s => s.trim()).filter(s => s);
      captionSegments = lines;
      audioDuration = Math.max(30, captionSegments.length * 10);
      ewLog(`${captionSegments.length} 个场景描述, 估算 ${audioDuration.toFixed(0)}s`, 'success');
      scriptText = captionSegments.join('\n');
    } else {
      // 口播模式
      if (script) {
        captionSegments = splitScriptToCaptions(script, splitMode);
        ewLog(`文案拆分为 ${captionSegments.length} 段`, 'success');
      }
      if (audioPath && captionSegments.length > 0) {
        const alignedText = captionSegments.join('\n');
        try {
          const r = await fetch(API_BASE() + '/api/stt/align', { method:'POST', headers:{'Content-Type':'application/json' }, body:JSON.stringify({audio_path:audioPath, transcript_text:alignedText}) });
          const d = await r.json();
          if (d.success) {
            sttSegments = d.segments || [];
            _lastSttSegments = sttSegments;
            const sttDuration = sttSegments.length ? sttSegments[sttSegments.length-1].end : 0;
            audioDuration = sttDuration;
            ewLog(`${sttSegments.length} 段对齐, STT ${sttDuration.toFixed(0)}s`, 'success');
          } else { ewLog(`STT 未对齐`, 'muted'); }
        } catch(ex) {
          console.error('[Clipwright] STT 对齐失败:', ex);
          ewLog(`STT 失败`, 'error');
        }
      }
      // 取 STT 时长和文件实际时长的较大值
      let realDuration = 0;
      if (audioPath) {
        realDuration = parseFloat(document.getElementById('ewAudioDuration')?.value || '0');
        if (!realDuration) {
          try {
            const probeResp = await fetch(API_BASE() + '/api/asset/probe?path=' + encodeURIComponent(audioPath));
            const probeData = await probeResp.json();
            if (probeData.duration_sec > 0) realDuration = probeData.duration_sec;
          } catch(_) {}
        }
        if (!realDuration) realDuration = parseFloat(document.getElementById('ewManualDuration')?.value || '0');
      }
      if (audioDuration < realDuration) { audioDuration = realDuration; }
      if (!audioDuration && script) audioDuration = Math.max(30, Math.min(1800, script.length / 5));
      if (!audioDuration) audioDuration = 60;
    }
    ewLog(`>> 音频时长: ${audioDuration.toFixed(0)}s`, 'title');

    // 为字幕段/场景段计算时间戳
    if (captionSegments.length > 0 && audioDuration > 0) {
      const totalChars = captionSegments.reduce((s, c) => s + (typeof c === 'string' ? c.length : c.text.length), 0);
      let cur = 0;
      captionSegments = captionSegments.map(seg => {
        const text = typeof seg === 'string' ? seg : seg.text;
        const dur = Math.max(1.0, (text.length / totalChars) * audioDuration);
        const result = { start_sec: cur, end_sec: cur + dur, text };
        cur += dur;
        return result;
      });
      if (captionSegments.length > 0) captionSegments[captionSegments.length - 1].end_sec = audioDuration;
    }

    // Step 2: 异步启动管线（传入完整文稿 + 音频时长）
    setEwProgress(20, '启动管线…');
    ewLog('[2/4] 启动 AI 管线…', 'step');
    const startRes = await fetch(API_BASE() + '/api/pipeline/run-async', { method:'POST', headers:{'Content-Type':'application/json' }, body:JSON.stringify({
      persona_id: personaId, category_plugin_id: pluginId, topic,
      extra_params: {
        material_source_ids: selectedSources,
        audio_duration_sec: audioDuration,
        split_mode: splitMode,
        script_text: scriptText,
        orientation: orientation,
        target_width: resolution[0],
        target_height: resolution[1],
        target_fps: renderFps,
        target_bitrate: renderBitrate,
        video_mode: videoMode,
      }, dry_run: false
    })});
    if (!startRes.ok) {
      const errBody = await startRes.text().catch(() => '');
      throw new Error(`启动管线失败 (HTTP ${startRes.status}): ${errBody.slice(0, 100)}`);
    }
    const startData = await startRes.json();
    const pipeline_id = startData?.pipeline_id;
    if (!pipeline_id) throw new Error('服务器未返回 pipeline_id');
    ewLog(`管线 ID: ${pipeline_id}`, 'muted');

    // Step 3: SSE 流实时追踪
    setEwProgress(30, '管线运行中…');
    ewLog('[3/4] 实时追踪中…', 'step');
    const evtSource = new EventSource(API_BASE() + '/api/pipeline/trace/stream/' + pipeline_id);
    let pipelineDone = false;

    await new Promise((resolve, reject) => {
      evtSource.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data);
          const iconMap = {llm:'🤖', tool:'🔧', skill:'🧠', plugin:'🔌', agent_start:'▶', agent_end:'✓', error:'✗', info:'○', timeline_snapshot:'📊'};
          const icon = iconMap[event.type] || '·';

          // Agent 开始
          if (event.type === 'agent_start') {
            ewLog(`▶ ${event.agent || event.summary}`, 'step');
          }
          // 时间线快照 → 实时渲染
          else if (event.type === 'timeline_snapshot' && event.detail) {
            _lastTimelineData = event.detail;
            renderTimelineWithCaptions(event.detail, sttSegments);
            ewLog(`📊 时间线更新: ${event.agent}`, 'muted');
            // 显示预览面板和播放按钮
            const panel = document.getElementById('ewPreviewPanel');
            const playBtn = document.getElementById('ewPlayBtn');
            if (panel) panel.style.display = 'block';
            if (playBtn) playBtn.style.display = 'inline-block';
            showEditChat();
          }
          // Agent 完成 → 更新进度
          else if (event.type === 'agent_end') {
            ewLog(`  ✓ ${event.agent}`, 'success');
          }
          // 错误
          else if (event.type === 'error') {
            ewLog(`  ✗ ${event.summary}`, 'error');
          }
          // 工具/LLM 调用
          else if (event.type === 'tool' || event.type === 'llm') {
            ewLog(`  ${icon} ${event.summary}`, 'muted');
          }
          // 工作流日志（INFO/WARNING/DEBUG 自动推送）
          else if (event.type === 'log' || event.type === 'info') {
            ewLog(`  ${event.summary}`, 'muted');
          }
          else if (event.type === 'warning') {
            ewLog(`  ⚠ ${event.summary}`, 'error');
          }
          // 管线完成（done 事件或 quality agent 完成）
          if (event.type === 'done' || (event.type === 'agent_end' && event.agent === 'quality')) {
            pipelineDone = true; evtSource.close(); resolve();
          }
          // 系统错误
          else if (event.type === 'error' && event.agent === 'system') {
            evtSource.close(); reject(new Error(event.summary));
          }
        } catch(ex) {
          console.error('[Clipwright] SSE 事件解析失败:', ex, '原始数据:', ev.data);
          ewLog(`SSE 事件解析错误: ${ex.message}`, 'error');
        }
      };
      evtSource.onerror = (err) => {
        console.error('[Clipwright] SSE 连接错误:', err);
        ewLog('SSE 连接中断，切换到轮询模式', 'error');
        pipelineDone = true; evtSource.close(); setTimeout(resolve, 300);
      };
      setTimeout(() => { if (!pipelineDone) { evtSource.close(); resolve(); } }, 600000); // 10 分钟超时
    });

    // 从管线结果中获取最终时间线（优先于 SSE 快照）
    ewLog('获取最终管线结果…', 'step');
    let finalTimeline = _lastTimelineData;
    try {
      const ac2 = new AbortController();
      const t2 = setTimeout(() => ac2.abort(), 360000);
      const r2 = await fetch(API_BASE() + '/api/pipeline/result/' + pipeline_id, { signal: ac2.signal });
      clearTimeout(t2);
      if (r2.ok) {
        const full = await r2.json();
        const ft = full?.shared_data?.final_timeline;
        if (ft && ft.tracks && ft.tracks.length > 0) {
          finalTimeline = ft;
          _lastTimelineData = ft;
          renderTimelineWithCaptions(ft, sttSegments);
          ewLog(`最终时间线: ${ft.tracks.length} 轨道, ${ft.duration_sec?.toFixed(1)}s`, 'success');
        }
      }
    } catch(ex) {
      console.error('[Clipwright] 获取管线结果失败:', ex);
      ewLog(`接口错误: ${ex.message}，使用 SSE 时间线`, 'error');
    }

    if (!finalTimeline || !finalTimeline.tracks || !finalTimeline.tracks.length) {
      ewLog('管线未生成时间线', 'error');
      try {
        const r3 = await fetch(API_BASE() + '/api/pipeline/trace/' + pipeline_id);
        const all = await r3.json();
        if (Array.isArray(all)) for (const ev of all) {
          if (ev.type === 'error' && ev.summary) ewLog(`  ${ev.summary}`, 'error');
        }
      } catch(ex) {
        console.error('[Clipwright] 获取错误追踪失败:', ex);
      }
      setEwProgress(0, '');
      const container = document.getElementById('ewProgress');
      if (container) container.style.display = 'none';
      btn.disabled = false; btn.textContent = '生成视频';
      return;
    }
    ewLog('管线完成', 'success');

    // Step 4: 显示渲染按钮，不再自动渲染
    setEwProgress(85, '管线完成，等待渲染…');
    ewLog('[4/4] 点击渲染按钮生成最终视频', 'step');
    // 保存管线结果，供渲染按钮使用
    _pipelineResult = { finalTimeline, sttSegments };
    // 显示渲染按钮
    const renderBtnRow = document.getElementById('ewRenderBtnRow');
    const renderBtn = document.getElementById('ewRenderBtn');
    const panel = document.getElementById('ewPreviewPanel');
    if (renderBtnRow && renderBtn && panel) {
      renderBtn.disabled = false;
      renderBtn.textContent = '渲染 MP4 视频';
      renderBtnRow.style.display = 'block';
      panel.style.display = 'block';
      const playBtn = document.getElementById('ewPlayBtn');
      if (playBtn) playBtn.style.display = 'inline-block';
      const queueBtn = document.getElementById('ewQueueBtn');
      if (queueBtn) queueBtn.style.display = 'block';
      showEditChat();
      ewLog('✅ 管线完成！可通过下方输入框对话调整视频', 'success');
    } else {
      // 如果 UI 元素不存在，回退自动渲染
      ewLog('渲染按钮不可用，自动渲染…', 'muted');
      await renderVideo();
    }

    setEwProgress(90, '等待用户点击渲染...');
  } catch(e) {
    console.error('[Clipwright] 管线运行异常:', e);
    ewLog(`错误: ${e.message}`, 'error');
    setEwProgress(0, '');
    const p = document.getElementById('ewProgress');
    if (p) p.style.display = 'none';
  }
  btn.disabled = false; btn.textContent = '生成视频';
}

// ── Timeline Renderer ──
function renderTimelineWithCaptions(tl, sttSegments) {
  if (!tl) return;
  const container = document.getElementById('timelineInner');
  const info = document.getElementById('tlInfo');
  const rangeEl = document.getElementById('tlRange');
  if (!container || !info) return;
  const dur = tl.duration_sec || 60;
  info.textContent = `${tl.width}x${tl.height} @ ${tl.fps}fps | ${dur.toFixed(1)}s`;
  const zoom = _tlZoom, pxPerSec = 100 * zoom, totalWidth = Math.max(dur * pxPerSec, 400);
  const trackH = 48, rulerH = 26, labelW = 70;
  let html = `<div style="display:flex;flex-direction:column;min-width:${labelW+totalWidth+10}px;user-select:none">`;
  html += `<div style="display:flex;height:${rulerH}px;position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border)">`;
  html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);font-size:9px;color:var(--text2);display:flex;align-items:center;justify-content:center">时间</div>`;
  html += `<div style="flex:1;position:relative;overflow:hidden">`;
  const tickInterval = Math.max(1, Math.floor(5 / zoom));
  for (let t = 0; t <= dur; t += tickInterval/2) {
    const x = t * pxPerSec; const isMain = t % tickInterval === 0;
    html += `<div style="position:absolute;left:${x}px;bottom:0;width:1px;height:${isMain?10:5}px;background:var(--border)"></div>`;
    if (isMain) html += `<div style="position:absolute;left:${x}px;bottom:12px;transform:translateX(-50%);font-size:9px;color:var(--text2)">${t.toFixed(1)}s</div>`;
  }
  html += `</div></div>`;
  const colors = {video:'#4f8cff', audio:'#34d399', text:'#fbbf24', caption:'#f59e0b', image:'#a855f7', animation:'#ff6b6b'};
  const trackNames = {video:'视频', audio:'音频', text:'文字', caption:'字幕', image:'图片', animation:'动画'};
  for (const track of (tl.tracks||[])) {
    const kind = track.kind || 'video', color = colors[kind] || '#888';
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">${trackNames[kind]||kind}</div>`;
    html += `<div style="flex:1;position:relative;min-width:${totalWidth}px">`;
    for (const clip of (track.clips||[])) {
      const x = (clip.start_sec||0) * pxPerSec, w = Math.max((clip.duration_sec||1) * pxPerSec, 4);
      const clipKind = clip.kind || 'video';
      const meta = clip.metadata || {};
      const label = clip.text || meta.label || meta.source_title || (clip.asset_id?clip.asset_id.split('/').pop().split('\\').pop().slice(0,20):'') || clip.id || '';
      const title = `${label} | ${clip.start_sec.toFixed(1)}s-${(clip.start_sec+clip.duration_sec).toFixed(1)}s`;
      const clipId = clip.id || `c_${Math.random().toString(36).slice(2,8)}`;

      const isVideoOrImage = (clipKind === 'video' || clipKind === 'image');
      const hasFilePath = clip.asset_id && (clip.asset_id.endsWith('.mp4') || clip.asset_id.endsWith('.mov') || clip.asset_id.endsWith('.avi') || clip.asset_id.endsWith('.mkv') || clip.asset_id.endsWith('.webm') || clip.asset_id.endsWith('.jpg') || clip.asset_id.endsWith('.png'));
      const thumbUrl = hasFilePath ? (API_BASE() + '/api/render/thumbnail?path=' + encodeURIComponent(clip.asset_id) + '&time_sec=' + Math.max(0, clip.start_sec || 0)) : '';

      // 可交互 clip：拖拽移动位置 + 点击选中
      const selAttr = `onclick="event.stopPropagation();selectTimelineClip('${clipId}','${label.slice(0,30)}',${clip.start_sec||0},${clip.duration_sec||1})"`;
      html += `<div data-clip-id="${clipId}" data-track="${kind}"
        style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;
               background:${color}44;border:1px solid ${color}88;
               display:flex;align-items:center;justify-content:center;overflow:hidden;
               cursor:grab;font-size:10px" title="${title}" ${selAttr}>`;
      // 左侧拖拽手柄
      html += `<div style="position:absolute;left:0;top:0;width:6px;height:100%;cursor:col-resize;background:transparent" title="拖动裁剪"></div>`;
      if (isVideoOrImage && hasFilePath && w > 40) {
        html += `<img src="${thumbUrl}" style="width:100%;height:100%;object-fit:cover" loading="lazy"
          onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
          <span style="display:none;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label.slice(0,15)}</span>`;
      } else if (w > 30) {
        html += `<span style="color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label.slice(0,15)}</span>`;
      }
      html += `</div>`;
    }
    html += `</div></div>`;
  }
  if (sttSegments && sttSegments.length > 0) {
    html += `<div style="display:flex;height:${trackH}px;border-bottom:1px solid var(--border);background:var(--surface2)">`;
    html += `<div style="width:${labelW}px;flex-shrink:0;border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text2);background:var(--surface)">配音</div>`;
    html += `<div style="flex:1;position:relative">`;
    for (const seg of sttSegments) {
      const x = (seg.start||0)*pxPerSec, w = Math.max(((seg.end||seg.start+1)-seg.start)*pxPerSec, 6);
      html += `<div style="position:absolute;left:${x}px;top:4px;width:${w}px;height:${trackH-8}px;border-radius:3px;background:#fbbf2444;border:1px solid #fbbf2488;display:flex;align-items:center;justify-content:center;overflow:hidden;font-size:10px;cursor:pointer" title="${seg.text}">`;
      if (w > 40) html += `<span style="color:#fff;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${seg.text}</span>`;
      html += `</div>`;
    }
    html += `</div></div>`;
  }
  // 播放头
  const phX = _previewTime * pxPerSec;
  html += `<div id="ewPlayhead" style="position:absolute;top:0;left:${phX}px;width:2px;height:100%;background:var(--accent);z-index:20;pointer-events:none;display:${_lastTimelineData?'block':'none'}"></div>`;
  html += '</div>';
  container.innerHTML = html;

  // 点击时间轴定位预览（如果点击的是空白区域）
  container.onclick = (e) => {
    // 如果点击的是 clip 内部，clip 的 onclick 已处理
    if (e.target.closest('[data-clip-id]')) return;
    const rect = container.getBoundingClientRect();
    const clickX = e.clientX - rect.left + container.parentElement.scrollLeft;
    const time = clickX / pxPerSec;
    seekPreview(time);
  };

  if (rangeEl) rangeEl.textContent = `${dur.toFixed(1)}s`;
}

function zoomTimeline(delta) {
  _tlZoom = Math.max(0.3, Math.min(5, _tlZoom + delta));
  if (_lastTimelineData) renderTimelineWithCaptions(_lastTimelineData, _lastSttSegments);
}

// ── 导出预设 ──
function applyPreset() {
  const preset = document.getElementById('ewPreset')?.value;
  if (!preset) return;
  const presets = {
    '1080p': { res: '1920x1080', fps: '30', bitrate: '5M' },
    '720p': { res: '1280x720', fps: '30', bitrate: '3M' },
    'bilibili': { res: '1920x1080', fps: '30', bitrate: '6M' },
    'youtube': { res: '1920x1080', fps: '30', bitrate: '8M' },
    'tiktok': { res: '1080x1920', fps: '30', bitrate: '4M' },
  };
  const p = presets[preset];
  if (p) {
    document.getElementById('ewResolution').value = p.res;
    document.getElementById('ewFps').value = p.fps;
    document.getElementById('ewBitrate').value = p.bitrate;
    ewLog(`应用预设: ${preset} (${p.res}, ${p.fps}fps, ${p.bitrate})`, 'success');
  }
}

// ── 项目保存/加载 ──
async function saveProject() {
  const data = {
    timeline: _lastTimelineData,
    pipelineResult: _pipelineResult,
    topic: document.getElementById('ewTopic')?.value,
    script: document.getElementById('ewScript')?.value,
    personaId: document.getElementById('ewPersonaId')?.value,
    pluginId: document.getElementById('ewPluginId')?.value,
    audioPath: document.getElementById('ewAudioPath')?.value,
    splitMode: document.getElementById('ewSplitMode')?.value,
  };
  try {
    const r = await fetch(API_BASE() + '/api/project/save?pipeline_id=manual_' + Date.now(), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: data, name: data.topic || '未命名项目' }),
    });
    const d = await r.json();
    ewLog(`项目已保存: ${d.project_id || d.project_id}`, 'success');
  } catch (ex) {
    ewLog(`保存失败: ${ex.message}`, 'error');
  }
}

async function loadProject() {
  try {
    const r = await fetch(API_BASE() + '/api/project/list');
    const list = await r.json();
    if (!list || !list.length) { ewLog('没有已保存的项目', 'error'); return; }
    // 取最新的项目
    const last = list[list.length - 1];
    await _loadProjectById(last.project_id);
  } catch (ex) {
    ewLog(`加载失败: ${ex.message}`, 'error');
  }
}

async function loadProjectList() {
  try {
    const r = await fetch(API_BASE() + '/api/project/list');
    const list = await r.json();
    const el = document.getElementById('ewProjectListContent');
    if (!el) return;
    if (!list || !list.length) {
      el.innerHTML = '<div style="color:var(--text2);padding:4px">无已保存的项目</div>';
      return;
    }
    el.innerHTML = list.slice().reverse().map(p =>
      `<div style="padding:3px 4px;cursor:pointer;border-bottom:1px solid var(--border)"
            onclick="_loadProjectById('${p.project_id}')"
            title="${p.name || p.project_id}">
        <span style="color:var(--text)">${(p.name || p.project_id).slice(0, 30)}</span>
        <span style="color:var(--text2);font-size:9px;float:right">${(p.created_at || '').slice(0, 10)}</span>
      </div>`
    ).join('');
  } catch(_) {}
}

async function _loadProjectById(projectId) {
  try {
    const r = await fetch(API_BASE() + '/api/project/load/' + projectId);
    const d = await r.json();
    const state = d.state || {};
    if (state.timeline) {
      _lastTimelineData = state.timeline;
      _pipelineResult = state.pipelineResult || null;
      renderTimelineWithCaptions(state.timeline, []);
      document.getElementById('ewPreviewPanel').style.display = 'block';
      document.getElementById('ewPlayBtn').style.display = 'inline-block';
      ewLog(`项目已加载`, 'success');
    }
    if (state.topic) document.getElementById('ewTopic').value = state.topic;
    if (state.script) document.getElementById('ewScript').value = state.script;
  } catch (ex) {
    ewLog(`加载失败: ${ex.message}`, 'error');
  }
}

// ── 渲染按钮（由用户手动触发）──
async function renderVideo() {
  if (!_pipelineResult || !_pipelineResult.finalTimeline) {
    ewLog('无可渲染的时间线，请先运行管线', 'error');
    return;
  }

  const renderBtn = document.getElementById('ewRenderBtn');
  const renderStatus = document.getElementById('ewRenderStatus');
  if (renderBtn) { renderBtn.disabled = true; renderBtn.textContent = '渲染中...'; }
  if (renderStatus) { renderStatus.style.display = 'block'; renderStatus.textContent = '渲染中...'; }

  const tl = _pipelineResult.finalTimeline;
  const sttSegments = _pipelineResult.sttSegments || [];

  // 预检：检查时间线轨道结构
  const trackInfo = (tl.tracks || []).map(t => `${t.name||t.kind}(${t.kind}):${(t.clips||[]).length}clips`).join(' | ') || '无轨道';
  const hasVideoClips = (tl.tracks || []).some(t => (t.clips || []).some(c => c.kind === 'video' || c.kind === 'image'));
  ewLog(`预检: ${trackInfo}`, 'muted');
  if (!hasVideoClips) {
    ewLog('警告: 时间线没有视频片段, 渲染可能生成空白视频', 'error');
  }

  try {
    setEwProgress(70, '渲染 MP4…');
    ewLog('渲染视频…', 'step');

    // 用 RenderRequest 包裹格式发送
    const audioPath = document.getElementById('ewAudioPath')?.value?.trim() || '';
    const reso = (document.getElementById('ewResolution')?.value || '1920x1080').split('x').map(Number);
    const preset = document.getElementById('ewPreset')?.value || '';
    const r = await fetch(API_BASE() + '/api/render/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        timeline: tl,
        output_path: 'renders/ai_edit_output.mp4',
        audio_file_path: audioPath || undefined,
        settings: {
          preset: preset || undefined,
          width: reso[0],
          height: reso[1],
          fps: parseInt(document.getElementById('ewFps')?.value || '30'),
          bitrate: document.getElementById('ewBitrate')?.value || '5M',
          audio_bitrate: '192k',
        },
      }),
    });
    const rd = await r.json();

    if (rd?.success) {
      ewLog(`渲染完成: ${(rd.duration_sec||0).toFixed(1)}s`, 'success');
      if (renderStatus) renderStatus.textContent = `渲染完成 ${(rd.duration_sec||0).toFixed(1)}s`;
      if (renderStatus) renderStatus.style.color = 'var(--green)';

      const video = document.getElementById('ewPreviewVideo');
      const src = document.getElementById('ewPreviewSrc');
      if (video && src) {
        src.src = API_BASE() + '/renders/ai_edit_output.mp4';
        video.load();
        ewLog('预览已更新', 'success');
      }
      showDownloadLink('ai_edit_output.mp4');
    } else {
      // FastAPI 返回 400 时 detail 在 rd.detail，非 rd.error
      const errMsg = rd?.error || rd?.detail || '需安装 FFmpeg 才能渲染 MP4';
      ewLog(`渲染: ${errMsg}`, (rd?.error || rd?.detail ? 'error' : 'muted'));
      if (renderStatus) renderStatus.textContent = `渲染失败: ${errMsg}`;
      if (renderStatus) renderStatus.style.color = 'var(--red)';
    }

    setEwProgress(100, '渲染完成');
  } catch(ex) {
    const errMsg = ex.message || '未知错误';
    console.error('[Clipwright] 渲染失败:', ex);
    ewLog(`渲染失败: ${errMsg}`, 'error');
    if (renderStatus) { renderStatus.textContent = `渲染异常: ${errMsg}`; renderStatus.style.color = 'var(--red)'; }
  } finally {
    if (renderBtn) { renderBtn.disabled = false; renderBtn.textContent = '重新渲染'; }
  }
}

// ── 时间轴预览（按层级/顺序实时预览）──
function seekPreview(time) {
  const tl = _lastTimelineData || (_pipelineResult ? _pipelineResult.finalTimeline : null);
  if (!tl) return;
  const dur = tl.duration_sec || 60;
  _previewTime = Math.max(0, Math.min(dur, time));
  _updatePreviewFrame(tl);
  _updatePlayhead();
  const timeEl = document.getElementById('ewPreviewTime');
  if (timeEl) timeEl.textContent = `${_previewTime.toFixed(1)}s`;
}

function _updatePlayhead() {
  const ph = document.getElementById('ewPlayhead');
  if (!ph || !_lastTimelineData) return;
  const zoom = _tlZoom, pxPerSec = 100 * zoom;
  ph.style.left = (_previewTime * pxPerSec) + 'px';
}

function _updatePreviewFrame(tl) {
  const frameImg = document.getElementById('ewFrameImg');
  const frameVideo = document.getElementById('ewPreviewPlayer');
  const frameDiv = document.getElementById('ewFramePreview');
  const placeholders = frameDiv ? frameDiv.querySelector('span') : null;
  if (!frameDiv) return;

  const tracks = tl.tracks || [];
  let foundClip = null;

  const sortedTracks = [...tracks].sort((a, b) => (a.index || 0) - (b.index || 0));
  for (const track of sortedTracks) {
    for (const clip of (track.clips || [])) {
      const cStart = clip.start_sec || 0;
      const cEnd = cStart + (clip.duration_sec || 1);
      if (_previewTime >= cStart && _previewTime < cEnd) {
        foundClip = { track, clip, offset: _previewTime - cStart };
        break;
      }
    }
    if (foundClip) break;
  }

  // 暂停之前的视频播放
  if (frameVideo && !frameVideo.paused) {
    frameVideo.pause();
  }

  // 清除旧的 overlay
  const oldOverlay = document.getElementById('ewPreviewOverlay');
  if (oldOverlay) oldOverlay.remove();

  if (foundClip) {
    const { clip, offset } = foundClip;
    const clipKind = clip.kind || '';
    const isVideo = clipKind === 'video' || clipKind === 'image';
    const hasFile = clip.asset_id && (clip.asset_id.includes('.mp4') || clip.asset_id.includes('.mov') || clip.asset_id.includes('.webm'));

    if (isVideo && hasFile) {
      const videoUrl = API_BASE() + '/api/render/video?path=' + encodeURIComponent(clip.asset_id);
      if (frameVideo) { frameVideo.style.display = 'block'; frameVideo.src = videoUrl; frameVideo.currentTime = offset + (clip.source_offset_sec || 0); frameVideo.play().catch(() => {}); }
      if (frameImg) frameImg.style.display = 'none';
      if (placeholders) placeholders.style.display = 'none';
    } else if (isVideo && clip.asset_id) {
      const thumbUrl = API_BASE() + '/api/render/thumbnail?path=' + encodeURIComponent(clip.asset_id) + '&time_sec=' + (offset + (clip.source_offset_sec || 0));
      if (frameImg) { frameImg.src = thumbUrl; frameImg.style.display = 'block'; }
      if (frameVideo) frameVideo.style.display = 'none';
      if (placeholders) placeholders.style.display = 'none';
    }

    // 查找当前时间重叠的 text/caption/animation clip，叠加显示
    const overlays = [];
    for (const t of (tl.tracks || [])) {
      const tKind = t.kind || '';
      if (tKind !== 'text' && tKind !== 'caption' && tKind !== 'animation') continue;
      for (const c of (t.clips || [])) {
        const cs = c.start_sec || 0;
        const ce = cs + (c.duration_sec || 1);
        if (_previewTime >= cs && _previewTime < ce) {
          overlays.push(c);
        }
      }
    }

    if (overlays.length > 0) {
      for (const oc of overlays) {
        const ok = oc.kind || '';
        const txt = oc.text || '';
        if (!txt) continue;
        const meta = oc.metadata || {};
        const isAnim = ok === 'animation';
        const fs = meta.font_size || (isAnim ? 72 : 48);
        const fc = meta.font_color || (isAnim ? '#ffd700' : '#ffffff');
        const pos = meta.position || (isAnim ? 'center' : 'bottom');
        const posStyle = pos === 'top' ? 'top:20px' : pos === 'bottom' ? 'bottom:20px' : 'top:50%;transform:translateY(-50%)';
        const sw = meta.stroke_width !== undefined ? meta.stroke_width : (isAnim ? 0 : 2);
        const strokeStyle = sw > 0 ? `-webkit-text-stroke:${sw}px #000;text-stroke:${sw}px #000;` : '';

        let animStyle = '';
        if (isAnim) {
          const at = meta.anim_type || 'fade_in';
          const animMap = {
            fade_in: 'animation:fadeIn 0.5s ease-in',
            slide_up: 'animation:slideUp 0.5s ease-out',
            zoom_in: 'animation:zoomIn 0.5s ease-out',
            glow: 'text-shadow:0 0 20px ' + fc + ',0 0 40px ' + fc,
          };
          animStyle = animMap[at] || animMap.fade_in;
        }

        const overlay = document.createElement('div');
        overlay.id = 'ewPreviewOverlay';
        overlay.textContent = txt;
        overlay.style.cssText = 'position:absolute;left:0;right:0;margin:auto;text-align:center;font-size:' + fs + 'px;color:' + fc + ';font-weight:bold;' + posStyle + ';pointer-events:none;z-index:10;' + strokeStyle + animStyle;
        frameDiv.appendChild(overlay);
        break; // 只显示第一个文字/字幕覆盖层
      }
    } else if (!isVideo) {
      // 既无视频也无文字覆盖层 → placeholder
      if (frameImg) frameImg.style.display = 'none';
      if (frameVideo) frameVideo.style.display = 'none';
      if (placeholders) {
        placeholders.style.display = 'block';
        placeholders.textContent = clip.text || clip.metadata?.label || clip.kind || '预览';
      }
    }
  } else {
    if (frameImg) frameImg.style.display = 'none';
    if (frameVideo) frameVideo.style.display = 'none';
    if (placeholders) {
      placeholders.style.display = 'block';
      placeholders.textContent = '—';
    }
  }
}

// ── 时间轴 clip 交互 ──
let _selectedClipId = null;

function selectTimelineClip(clipId, label, start, dur) {
  _selectedClipId = clipId;
  // 高亮选中的 clip
  document.querySelectorAll('[data-clip-id]').forEach(el => {
    if (el.dataset.clipId === clipId) {
      el.style.borderColor = 'var(--accent)';
      el.style.borderWidth = '2px';
      el.style.zIndex = '5';
    } else {
      el.style.borderColor = '';
      el.style.borderWidth = '1px';
      el.style.zIndex = '';
    }
  });
  seekPreview(start);
  ewLog(`选中: ${label} (${start.toFixed(1)}s-${(start+dur).toFixed(1)}s)`, 'muted');
}

function toggleTimelinePreview() {
  const btn = document.getElementById('ewPlayBtn');
  const player = document.getElementById('ewPreviewPlayer');
  if (_previewPlaying) {
    _previewPlaying = false;
    if (_previewTimer) { clearInterval(_previewTimer); _previewTimer = null; }
    if (player) player.pause();
    if (btn) btn.textContent = '▶ 播放';
  } else {
    const tl = _lastTimelineData || (_pipelineResult ? _pipelineResult.finalTimeline : null);
    if (!tl) return;
    const dur = tl.duration_sec || 60;
    if (_previewTime >= dur) _previewTime = 0;
    _previewPlaying = true;
    if (btn) btn.textContent = '⏸ 暂停';
    // 先 seek 到当前位置（触发视频播放）
    seekPreview(_previewTime);
    _previewTimer = setInterval(() => {
      _previewTime += 1/30;
      const tDur = (_lastTimelineData || (_pipelineResult ? _pipelineResult.finalTimeline : null))?.duration_sec || 60;
      if (_previewTime >= tDur) {
        _previewTime = tDur;
        _previewPlaying = false;
        clearInterval(_previewTimer); _previewTimer = null;
        if (btn) btn.textContent = '▶ 播放';
      }
      seekPreview(_previewTime);
    }, 1000/30);
  }
}

// ── 队列渲染 + 下载 ──
async function queueRender() {
  const tl = _pipelineResult?.finalTimeline;
  if (!tl) { ewLog('无可渲染的时间线', 'error'); return; }
  try {
    const audioPath = document.getElementById('ewAudioPath')?.value?.trim() || '';
    const preset = document.getElementById('ewPreset')?.value || '';
    const reso = (document.getElementById('ewResolution')?.value || '1920x1080').split('x').map(Number);
    const r = await fetch(API_BASE() + '/api/render/queue', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        timeline: tl, output_path: '',
        audio_file_path: audioPath || undefined,
        settings: {
          preset: preset || undefined,
          width: reso[0], height: reso[1],
          fps: parseInt(document.getElementById('ewFps')?.value || '30'),
          bitrate: document.getElementById('ewBitrate')?.value || '5M',
          audio_bitrate: '192k',
        },
      }),
    });
    const d = await r.json();
    ewLog(`已加入队列: ${d.task_id} → ${d.output}`, 'success');
    // 轮询状态
    const checkTask = setInterval(async () => {
      try {
        const qr = await fetch(API_BASE() + '/api/render/queue/' + d.task_id);
        const qd = await qr.json();
        if (qd.status === 'completed') {
          clearInterval(checkTask);
          ewLog(`队列渲染完成: ${qd.output_path}`, 'success');
          const link = document.getElementById('ewDownloadLink');
          const row = document.getElementById('ewDownloadRow');
          if (link && row) {
            link.href = API_BASE() + '/api/render/download/' + d.task_id + '.mp4';
            row.style.display = 'block';
          }
        } else if (qd.status === 'failed') {
          clearInterval(checkTask);
          ewLog(`队列渲染失败: ${qd.result?.error || '未知错误'}`, 'error');
        }
      } catch(_) {}
    }, 2000);
  } catch(ex) {
    ewLog(`队列提交失败: ${ex.message}`, 'error');
  }
}

function showDownloadLink(filename) {
  const link = document.getElementById('ewDownloadLink');
  const row = document.getElementById('ewDownloadRow');
  if (link && row) {
    link.href = API_BASE() + '/api/render/download/' + encodeURIComponent(filename);
    row.style.display = 'block';
  }
}

// ── 项目列表 ──
// ── 对话式编辑聊天 ──
let _editSessionId = null;

function showEditChat() {
  const row = document.getElementById('ewChatArea');
  if (row) row.style.display = 'flex';
}

async function ensureEditSession() {
  if (_editSessionId) return _editSessionId;
  try {
    const audioPath = document.getElementById('ewAudioPath')?.value?.trim() || '';
    const r = await fetch(API_BASE() + '/api/edit/session/create', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        timeline: _lastTimelineData || (_pipelineResult?.finalTimeline || null),
        video_path: audioPath || undefined,
      }),
    });
    const d = await r.json();
    _editSessionId = d.session_id;
    ewLog('编辑会话已创建', 'muted');
    return _editSessionId;
  } catch(ex) {
    ewLog('创建编辑会话失败: ' + ex.message, 'error');
    return null;
  }
}

async function sendEditChat() {
  const input = document.getElementById('ewChatInput');
  if (!input) return;
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  input.disabled = true;
  ewLog('🎯 ' + msg, 'step');

  const sessionId = await ensureEditSession();
  if (!sessionId) { input.disabled = false; return; }

  try {
    const r = await fetch(API_BASE() + '/api/edit/session/' + sessionId + '/chat?message=' + encodeURIComponent(msg), { method: 'POST' });
    const d = await r.json();
    if (d.thinking) ewLog('🤔 ' + d.thinking.slice(0, 300), 'muted');
    if (d.llm_reply) ewLog('🤖 ' + d.llm_reply, d.success ? 'success' : 'error');
    if (d.success && d.output) {
      ewLog('  执行结果: ' + JSON.stringify(d.output).slice(0, 120), 'muted');
    } else if (!d.success && d.error) {
      ewLog('  失败: ' + d.error, 'error');
    }
  } catch(ex) {
    ewLog('请求失败: ' + ex.message, 'error');
  } finally {
    input.disabled = false;
  }
}

function quickEdit(msg) {
  const input = document.getElementById('ewChatInput');
  if (input) input.value = msg;
  sendEditChat();
}

// 增强 ewLog 流式输出
const _origEwLog = ewLog;
ewLog = function(msg, type) {
  _origEwLog(msg, type);
  // 更新 badge 计数
  const badge = document.getElementById('ewLogBadge');
  if (badge) {
    const count = parseInt(badge.textContent || '0') + 1;
    badge.textContent = count + '条';
    badge.style.display = 'inline';
  }
};

async function listProjects() {
  try {
    const r = await fetch(API_BASE() + '/api/project/list');
    return await r.json();
  } catch(_) { return []; }
}
