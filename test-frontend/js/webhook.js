/* Webhook 管理 */

async function whList() {
  const el = document.getElementById('whList');
  if (!el) return;
  try {
    const r = await api('GET', '/api/webhook/subscriptions');
    const data = r.data || {};
    let html = '';
    for (const [eventType, subs] of Object.entries(data)) {
      if (!subs || !subs.length) continue;
      html += `<div style="margin-bottom:8px"><strong style="font-size:11px">${eventType}</strong></div>`;
      subs.forEach(s => {
        html += `<div style="padding:4px 8px;margin-bottom:4px;background:var(--surface2);border-radius:4px;font-size:11px">
          <span>${s.url}</span>
          <button class="btn btn-danger btn-sm" style="font-size:9px;padding:1px 6px;float:right" onclick="whUnsubscribe('${eventType}','${s.url}')">取消</button>
        </div>`;
      });
    }
    el.innerHTML = html || '<div class="text-muted" style="padding:10px">无订阅</div>';
  } catch(_) { el.innerHTML = '<div style="color:var(--red);padding:10px">加载失败</div>'; }
}

async function whSubscribe() {
  const eventType = document.getElementById('whEventType').value;
  const url = document.getElementById('whUrl').value.trim();
  if (!url) { alert('请输入回调 URL'); return; }
  try {
    const r = await api('POST', '/api/webhook/subscribe?event_type=' + eventType + '&url=' + encodeURIComponent(url));
    if (r.ok) { alert('已订阅'); whList(); }
    else alert('订阅失败');
  } catch(ex) { alert('异常: ' + ex.message); }
}

async function whUnsubscribe(eventType, url) {
  if (!confirm('取消订阅 ' + url + '?')) return;
  try {
    await api('POST', '/api/webhook/unsubscribe?event_type=' + eventType + '&url=' + encodeURIComponent(url));
    whList();
  } catch(_) {}
}

async function whTest() {
  try {
    const r = await api('POST', '/api/webhook/test/pipeline');
    alert('测试结果: ' + JSON.stringify(r.data?.results || []));
  } catch(_) {}
}
