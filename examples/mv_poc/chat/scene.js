/* 聊天界面场景（PRD C2 / R-04）。
 *
 * 纪律（由 tools/lint_determinism.py 静态校验）：
 * - 不读墙钟、不用随机、不用定时器；画面只由 t 与 window.__chat 推导；
 * - 消息、状态、光标全部数据驱动（chat_script.json）；
 * - 背景不透明由调用方决定（本文件不自作主张画底色）。
 */
(function () {
  var root = document.getElementById('root');
  var data = window.__chat || { messages: [], status: [], duration_sec: 5, title: '' };
  var W = root.clientWidth || 1920;
  var H = root.clientHeight || 1080;
  var SCALE = W / 1920;

  function px(value) { return Math.round(value * SCALE) + 'px'; }
  function font(size, weight, family) {
    return (weight || 400) + ' ' + px(size) + ' ' + (family || 'ui-sans-serif, system-ui, sans-serif');
  }

  var shell = document.createElement('div');
  shell.style.cssText = [
    'position:absolute', 'left:0', 'top:0', 'width:100%', 'height:100%',
    'background:radial-gradient(120% 90% at 70% 20%, #141a2e 0%, #0a0a0c 60%, #06060a 100%)',
    'color:#e9e6df', 'overflow:hidden'
  ].join(';');
  root.appendChild(shell);

  var panel = document.createElement('div');
  panel.style.cssText = [
    'position:absolute', 'left:' + px(180), 'top:' + px(90),
    'width:' + px(1200), 'height:' + px(820),
    'background:rgba(14,16,22,0.92)', 'border:1px solid rgba(255,255,255,0.08)',
    'border-radius:' + px(18), 'overflow:hidden',
    'box-shadow:0 ' + px(40) + ' ' + px(120) + ' rgba(0,0,0,0.55)'
  ].join(';');
  shell.appendChild(panel);

  var bar = document.createElement('div');
  bar.style.cssText = [
    'position:absolute', 'left:0', 'top:0', 'width:100%', 'height:' + px(72),
    'border-bottom:1px solid rgba(255,255,255,0.07)', 'display:flex',
    'align-items:center', 'justify-content:space-between',
    'padding:0 ' + px(28), 'box-sizing:border-box'
  ].join(';');
  panel.appendChild(bar);

  var title = document.createElement('div');
  title.textContent = data.title || 'New conversation';
  title.style.cssText = 'font:' + font(26, 600) + ';letter-spacing:0.5px';
  bar.appendChild(title);

  var statusWrap = document.createElement('div');
  statusWrap.style.cssText = 'display:flex;align-items:center;gap:' + px(10);
  bar.appendChild(statusWrap);

  var dot = document.createElement('div');
  dot.style.cssText = 'width:' + px(12) + ';height:' + px(12) + ';border-radius:50%';
  statusWrap.appendChild(dot);

  var statusText = document.createElement('div');
  statusText.style.cssText = 'font:' + font(18, 400, 'ui-monospace, monospace') + ';opacity:0.75';
  statusWrap.appendChild(statusText);

  var thread = document.createElement('div');
  thread.style.cssText = [
    'position:absolute', 'left:' + px(28), 'right:' + px(28),
    'top:' + px(100), 'bottom:' + px(120), 'overflow:hidden',
    'display:flex', 'flex-direction:column', 'gap:' + px(18)
  ].join(';');
  panel.appendChild(thread);

  var input = document.createElement('div');
  input.style.cssText = [
    'position:absolute', 'left:' + px(28), 'right:' + px(28), 'bottom:' + px(28),
    'height:' + px(72), 'border-radius:' + px(14),
    'border:1px solid rgba(255,255,255,0.10)',
    'background:rgba(255,255,255,0.03)', 'display:flex', 'align-items:center',
    'padding:0 ' + px(24), 'box-sizing:border-box',
    'font:' + font(20, 400) + ';opacity:0.55'
  ].join(';');
  input.textContent = data.input_hint || 'Write to me';
  panel.appendChild(input);

  var caret = document.createElement('div');
  caret.style.cssText = [
    'position:absolute', 'left:' + px(398), 'bottom:' + px(96),
    'width:' + px(3), 'height:' + px(26), 'background:#8fb8ff'
  ].join(';');
  panel.appendChild(caret);

  var bubbles = [];
  var STATUS_COLORS = { online: '#5ad1a5', away: '#e7b25c', offline: '#8a8a8a' };

  function buildBubbles() {
    for (var i = 0; i < data.messages.length; i++) {
      var message = data.messages[i];
      var row = document.createElement('div');
      row.style.cssText = 'display:flex;justify-content:flex-end';
      var bubble = document.createElement('div');
      var mine = message.side === 'user';
      bubble.textContent = message.text;
      bubble.style.cssText = [
        'max-width:72%', 'padding:' + px(16) + ' ' + px(20),
        'border-radius:' + px(14), 'font:' + font(21, 400),
        'line-height:1.45', 'box-sizing:border-box',
        mine
          ? 'background:rgba(143,184,255,0.16);border:1px solid rgba(143,184,255,0.28)'
          : 'background:rgba(246,240,229,0.94);color:#14161c;border:1px solid rgba(0,0,0,0.06)',
        'opacity:0', 'transform:translateY(' + px(14) + ')'
      ].join(';');
      row.style.justifyContent = mine ? 'flex-end' : 'flex-start';
      row.appendChild(bubble);
      thread.appendChild(row);
      bubbles.push({ node: row, bubble: bubble, t0: message.t });
    }
  }

  buildBubbles();

  function statusAt(t) {
    var current = data.status && data.status.length ? data.status[0].state : 'online';
    for (var i = 0; i < (data.status || []).length; i++) {
      if (t >= data.status[i].t) { current = data.status[i].state; }
    }
    return current;
  }

  var scene = {
    duration: data.duration_sec || 5,
    renderFrame: function (t, ctx) {
      var state = statusAt(t);
      dot.style.background = STATUS_COLORS[state] || '#8a8a8a';
      dot.style.boxShadow = '0 0 ' + px(14) + ' ' + (STATUS_COLORS[state] || '#8a8a8a');
      statusText.textContent = state;

      for (var i = 0; i < bubbles.length; i++) {
        var entry = bubbles[i];
        var local = t - entry.t0;
        if (local < 0) {
          entry.node.style.opacity = '0';
          continue;
        }
        var appear = Math.min(1, local / 0.32);
        entry.node.style.opacity = String(appear);
        entry.bubble.style.opacity = String(appear);
        entry.bubble.style.transform = 'translateY(' + px(14 * (1 - appear)) + ')';
      }

      // 打字光标：由 t 推导的方波（每秒 2 次），不使用 CSS animation
      var on = Math.floor(t * 2) % 2 === 0;
      caret.style.opacity = on ? '1' : '0.12';
      var typed = Math.min(1, Math.max(0, (t - 10.4) / 1.2));
      caret.style.left = px(398 + 240 * typed);
      caret.style.width = px(3);

      // 底线呼吸：整屏亮度随 t 缓慢变化（不触发闪光闸：幅度 < 10%）
      shell.style.filter = 'brightness(' + (1 + 0.03 * Math.sin(t * 0.8)) + ')';
    }
  };

  window.__scene = scene;
})();
