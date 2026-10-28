/* 版画镜头（PRD C3 / R-06…R-10 的集成验证）。
 *
 * 数据来源：window.__plate = { ground, clean, misreg, glyph, lines[], grid }
 * 图片均为 base64 data URI（由 scripts/render_plate_shot.py 注入）——
 * 这样合成是自包含的，不依赖文件服务器路径，也不触网。
 *
 * 纪律：画面只由 t 推导；亮度只升不降（棘轮），避免触发闪光闸。
 */
(function () {
  var root = document.getElementById('root');
  var data = window.__plate || {};
  var W = root.clientWidth || 960;
  var H = root.clientHeight || 540;
  var SCALE = W / 960;

  function px(value) { return Math.round(value * SCALE) + 'px'; }

  var stage = document.createElement('div');
  stage.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;overflow:hidden;background:#0a0a0c';
  root.appendChild(stage);

  function layer(src, opacity, z) {
    var node = document.createElement('img');
    node.src = src;
    node.style.cssText = [
      'position:absolute', 'left:0', 'top:0', 'width:100%', 'height:100%',
      'object-fit:cover', 'opacity:' + opacity, 'z-index:' + (z || 1)
    ].join(';');
    stage.appendChild(node);
    return node;
  }

  var ground = layer(data.ground, 1, 1);
  // 字符版画纹理必须压在三墨之上（否则被不透明墨层盖住，等于没画）
  var clean = layer(data.clean, 0, 2);
  var misreg = layer(data.misreg, 0, 2);
  var glyph = layer(data.glyph, 0, 3);

  var band = document.createElement('div');
  band.style.cssText = [
    'position:absolute', 'left:' + px(40), 'bottom:' + px(44), 'right:' + px(40),
    'font:400 ' + px(30) + '/1.35 Georgia, "Songti SC", serif',
    'color:#f2ead8', 'text-shadow:0 ' + px(2) + ' ' + px(10) + ' rgba(0,0,0,0.6)',
    'opacity:0', 'z-index:5'
  ].join(';');
  stage.appendChild(band);

  var keyword = document.createElement('div');
  keyword.style.cssText = [
    'position:absolute', 'left:0', 'right:0', 'top:' + px(180),
    'text-align:center', 'font:700 ' + px(96) + '/1 Georgia, "Songti SC", serif',
    'letter-spacing:' + px(6), 'color:#f6f0e5', 'opacity:0', 'z-index:4'
  ].join(';');
  stage.appendChild(keyword);

  var lines = data.lines || [];
  var cutAt = data.cut_at || 2.5;

  function lineAt(t) {
    for (var i = 0; i < lines.length; i++) {
      if (t >= lines[i].t0 && t < lines[i].t1) { return lines[i]; }
    }
    return null;
  }

  // V31：歌词文本只从 texts[i] 取（不把整段歌词塞进每行对象里）
  function lineText(line) {
    if (!line) { return ''; }
    var texts = data.texts || {};
    return texts[String(line.i)] || '';
  }

  function ease(x) { return Math.max(0, Math.min(1, x)); }

  var scene = {
    duration: data.duration_sec || 6,
    renderFrame: function (t) {
      // 版画进入：ground 常驻，glyph 纹理在 0.4–1.0s 淡入（压在墨层上，形成"字符版画"质感），
      // 套印在 1.0s 上版
      glyph.style.opacity = String(0.32 * ease((t - 0.4) / 0.6));
      var inked = ease((t - 1.0) / 0.5);
      var broken = t >= cutAt ? 1 : 0;
      clean.style.opacity = String(inked * (1 - broken));
      misreg.style.opacity = String(inked * broken);

      // 关键词：2.0s 起 0.25s 内出现，之后保持
      keyword.style.opacity = String(ease((t - 2.0) / 0.25));
      keyword.textContent = data.keyword || '';

      // 字幕带：跟着歌词行（行级时间），换行时重设
      var line = lineAt(t);
      if (line) {
        band.textContent = lineText(line);
        band.style.opacity = '1';
      } else {
        band.style.opacity = '0';
      }

      // 亮度棘轮：只升不降，且幅度 < 10%（不触发闪光闸）
      var lift = 0.06 * ease(t / 3.0);
      stage.style.filter = 'brightness(' + (1 + lift) + ')';
    }
  };

  window.__scene = scene;
})();
