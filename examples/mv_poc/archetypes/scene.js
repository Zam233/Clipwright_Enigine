/* 形态样例场景（V49 部分兑现）：纯代码绘制，不需要外部素材。
 *
 * 用 window.__arch = { kind, duration_sec, title } 切换形态：
 *   typography  — 排版/文字动画
 *   generative  — 生成艺术（几何线阵 + 粒子）
 *   code_data   — 代码清单 + 逐位计数
 *   vj_light    — 光条/径向光（亮度只升不降，避免频闪）
 *
 * 纪律：不含歌词文本、不读墙钟、不用随机（需要随机时用 ctx.prng）、不用定时器。
 */
(function () {
  var root = document.getElementById('root');
  var data = window.__arch || { kind: 'typography', duration_sec: 10, title: 'ARCHETYPE' };
  var W = root.clientWidth || 960;
  var H = root.clientHeight || 540;
  var SCALE = W / 960;
  var DURATION = data.duration_sec || 10;

  function px(v) { return Math.round(v * SCALE) + 'px'; }
  function font(size, weight, family) {
    return (weight || 400) + ' ' + px(size) + ' ' + (family || 'Georgia, "Songti SC", serif');
  }
  function ease(x) { return Math.max(0, Math.min(1, x)); }

  var stage = document.createElement('div');
  stage.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;overflow:hidden;background:#08080b';
  root.appendChild(stage);

  function el(css, text) {
    var node = document.createElement('div');
    node.style.cssText = css;
    if (text !== undefined) { node.textContent = text; }
    stage.appendChild(node);
    return node;
  }

  var kind = String(data.kind || 'typography');
  var title = String(data.title || 'ARCHETYPE');
  var scene = { duration: DURATION, renderFrame: function () {} };

  if (kind === 'typography') {
    var words = ['one', 'set', 'of', 'points'];
    var boxes = [];
    for (var i = 0; i < words.length; i++) {
      boxes.push(el([
        'position:absolute', 'left:' + px(80), 'top:' + px(120 + i * 84),
        'font:' + font(64, 700), 'letter-spacing:' + px(2), 'color:#f2ead8', 'opacity:0'
      ].join(';'), words[i]));
    }
    var kicker = el([
      'position:absolute', 'left:' + px(80), 'bottom:' + px(48),
      'font:' + font(18, 400, 'ui-monospace, monospace'), 'color:#8a8a8a', 'opacity:0'
    ].join(';'), title);
    scene.renderFrame = function (t) {
      for (var i = 0; i < boxes.length; i++) {
        var appear = ease((t - i * 0.45) / 0.35);
        boxes[i].style.opacity = String(appear);
        boxes[i].style.transform = 'translateX(' + px(28 * (1 - appear)) + ')';
      }
      kicker.style.opacity = String(0.8 * ease((t - 2.2) / 0.6));
    };
  } else if (kind === 'lyric_graphic') {
    /* 纯图形歌词（V49 第 7 类形态）：没有外部素材，全靠排版 + 逐词高亮 + 版面网格。
     * 歌词文本来自 data.lines（数据文件），**不写在本文件里**——V31 的纪律。
     * 高亮进度由 t 推导，不用定时器；亮度只升不降，避免触发闪光闸。 */
    var lyricLines = (data.lines && data.lines.length) ? data.lines : [];
    var grid = document.createElement('canvas');
    grid.width = W; grid.height = H;
    grid.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%';
    stage.appendChild(grid);
    var g2d = grid.getContext('2d');
    var lineNodes = [];
    for (var li = 0; li < lyricLines.length; li++) {
      var words = String(lyricLines[li].text || '').split(/\s+/);
      var row = el([
        'position:absolute', 'left:' + px(80), 'top:' + px(132 + li * 76),
        'font:' + font(46, 700), 'letter-spacing:' + px(1),
        'color:#f2ead8', 'opacity:0', 'white-space:pre'
      ].join(';'));
      var spans = [];
      for (var wi = 0; wi < words.length; wi++) {
        var sp = document.createElement('span');
        sp.textContent = words[wi] + (wi < words.length - 1 ? ' ' : '');
        sp.style.opacity = '0.28';
        row.appendChild(sp);
        spans.push(sp);
      }
      lineNodes.push({ row: row, spans: spans, t0: Number(lyricLines[li].t0 || 0), t1: Number(lyricLines[li].t1 || 0) });
    }
    var tagL = el([
      'position:absolute', 'left:' + px(80), 'bottom:' + px(48),
      'font:' + font(16, 400, 'ui-monospace, monospace'), 'color:#8a8a8a', 'opacity:0'
    ].join(';'), title);
    scene.renderFrame = function (t) {
      // 版面：竖线栅格 + 一条随进度推进的墨线（纯图形，不依赖素材）
      var progress = ease(t / Math.max(DURATION, 0.001));
      g2d.clearRect(0, 0, W, H);
      g2d.fillStyle = '#0b0a08';
      g2d.fillRect(0, 0, W, H);
      g2d.strokeStyle = 'rgba(242,234,216,0.10)';
      g2d.lineWidth = Math.max(1, W / 900);
      for (var c = 0; c < 9; c++) {
        var x = W * (0.08 + 0.105 * c);
        g2d.beginPath();
        g2d.moveTo(x, H * 0.08);
        g2d.lineTo(x, H * 0.92);
        g2d.stroke();
      }
      g2d.fillStyle = 'rgba(200,60,48,0.85)';
      g2d.fillRect(W * 0.08, H * (0.90 - 0.62 * progress), W * 0.84, Math.max(2, H / 260));
      for (var i = 0; i < lineNodes.length; i++) {
        var item = lineNodes[i];
        var lineOn = ease((t - item.t0) / 0.25);
        item.row.style.opacity = String(lineOn);
        var span = Math.max(0.001, (item.t1 - item.t0));
        var local = ease((t - item.t0) / span);
        for (var s = 0; s < item.spans.length; s++) {
          // 逐词点亮：第 s 个词在 local 走过 s/(n+1) 时点亮，只升不降
          var lit = local >= (s + 1) / (item.spans.length + 1) ? 1 : 0.28;
          item.spans[s].style.opacity = String(lit);
          item.spans[s].style.color = lit === 1 ? '#f2ead8' : '#8f887a';
        }
      }
      tagL.style.opacity = String(0.75 * ease((t - 1.2) / 0.8));
    };
  } else if (kind === 'generative') {
    var canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    canvas.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%';
    stage.appendChild(canvas);
    var ctx2d = canvas.getContext('2d');
    scene.renderFrame = function (t, ctx) {
      var progress = ease(t / Math.max(DURATION, 0.001));
      ctx2d.fillStyle = '#08080b';
      ctx2d.fillRect(0, 0, W, H);
      ctx2d.lineWidth = Math.max(1, W / 640);
      var lines = 42;
      for (var i = 0; i < lines; i++) {
        var p = i / lines;
        var hue = 28 + 18 * Math.sin(p * 6.28 + progress * 3.14);
        ctx2d.strokeStyle = 'hsla(' + hue.toFixed(1) + ', 85%, 62%, ' + (0.15 + 0.5 * p) + ')';
        ctx2d.beginPath();
        var y = H * (0.12 + 0.76 * p);
        ctx2d.moveTo(0, y);
        ctx2d.lineTo(W * ease(progress * (0.4 + p)), y - H * 0.12 * Math.sin(progress * 3.14 + p * 5));
        ctx2d.stroke();
      }
      // 结论：粒子位置由 t 推导（不使用随机）
      for (var j = 0; j < 24; j++) {
        var ang = (j / 24) * 6.283 + progress * 1.6;
        var r = Math.min(W, H) * (0.10 + 0.22 * progress);
        var x = W / 2 + Math.cos(ang) * r;
        var y2 = H / 2 + Math.sin(ang) * r * 0.7;
        ctx2d.fillStyle = 'rgba(242,234,216,' + (0.25 + 0.55 * progress) + ')';
        ctx2d.beginPath();
        ctx2d.arc(x, y2, Math.max(1.2, W / 500), 0, 6.283);
        ctx2d.fill();
      }
    };
  } else if (kind === 'code_data') {
    var lines = [
      'while (you.presence != "online") {',
      '    love = solve(me, you);',
      '    send(love, you);',
      '    wait(you.reply);',
      '    run++;',
      '}',
      'say(love);  // unreachable'
    ];
    var codeBoxes = [];
    for (var k = 0; k < lines.length; k++) {
      codeBoxes.push(el([
        'position:absolute', 'left:' + px(70), 'top:' + px(90 + k * 34),
        'font:' + font(18, 400, 'ui-monospace, Consolas, monospace'),
        'color:#cfd6e4', 'white-space:pre', 'opacity:0.25'
      ].join(';'), lines[k]));
    }
    var counter = el([
      'position:absolute', 'right:' + px(70), 'bottom:' + px(60),
      'font:' + font(52, 700, 'ui-monospace, Consolas, monospace'), 'color:#f5506f'
    ].join(';'), 'run 0');
    var tag = el([
      'position:absolute', 'left:' + px(70), 'bottom:' + px(60),
      'font:' + font(16, 400, 'ui-monospace, Consolas, monospace'), 'color:#8a8a8a'
    ].join(';'), title);
    scene.renderFrame = function (t) {
      var loop = Math.min(lines.length - 1, Math.floor(t * 1.2) % lines.length);
      for (var i = 0; i < codeBoxes.length; i++) {
        codeBoxes[i].style.opacity = i === loop ? '1' : '0.25';
      }
      counter.textContent = 'run ' + String(Math.floor(t * 1.2));
      tag.style.opacity = '0.75';
    };
  } else {
    var glow = el([
      'position:absolute', 'left:' + px(360), 'top:' + px(180),
      'width:' + px(240), 'height:' + px(240), 'border-radius:50%',
      'background:radial-gradient(circle, rgba(242,234,216,0.9) 0%, rgba(255,138,61,0.35) 45%, rgba(8,8,11,0) 72%)',
      'opacity:0.35'
    ].join(';'));
    var bars = [];
    for (var b = 0; b < 5; b++) {
      bars.push(el([
        'position:absolute', 'left:' + px(60 + b * 170), 'width:' + px(4),
        'top:' + px(60), 'height:' + px(60), 'background:#f2ead8', 'opacity:0.2'
      ].join(';')));
    }
    scene.renderFrame = function (t) {
      // 亮度棘轮：只升不降（不触发闪光闸）
      var lift = ease(t / Math.max(DURATION, 0.001));
      glow.style.opacity = String(0.35 + 0.5 * lift);
      for (var i = 0; i < bars.length; i++) {
        var h = 60 + 300 * lift * (0.4 + 0.15 * i);
        bars[i].style.height = px(h);
        bars[i].style.opacity = String(0.2 + 0.5 * lift);
      }
    };
  }

  window.__scene = scene;
})();
