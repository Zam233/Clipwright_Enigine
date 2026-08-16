/**
 * Timeline renderers — pure Canvas 2D drawing functions.
 * Aesthetic: Premiere Pro density + Material You color science.
 */
import type { Track, Clip, ClipKind } from '@/types/timeline';
import { TRACK_COLORS } from '@/types/timeline';
import type { TimelineLayout, Marker } from './types';
import { timeToX, trackToY, scrollbarGeom } from './types';
import { mediaManager } from '@/services/media/mediaManager';
import { useSettingsStore } from '@/stores/settingsStore';
import { usePreviewStore } from '@/stores/previewStore';

const MONO = "'JetBrains Mono','SF Mono','Consolas',monospace";
const SANS = "'Inter','Noto Sans SC',sans-serif";

// ── color helpers ──────────────────────────────────────
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}
function rgbToHex(r: number, g: number, b: number): string {
  const c = (v: number) =>
    Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}
function rgba(hex: string, a: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}
function lighten(hex: string, amt: number): string {
  const [r, g, b] = hexToRgb(hex);
  const f = (v: number) => Math.min(255, Math.round(v + (255 - v) * amt));
  return rgbToHex(f(r), f(g), f(b));
}
function shade(hex: string, amt: number): string {
  const [r, g, b] = hexToRgb(hex);
  const f = (v: number) => Math.round(v * (1 - amt));
  return rgbToHex(f(r), f(g), f(b));
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

// ── Background & grid ──────────────────────────────────
export function drawBackground(ctx: CanvasRenderingContext2D, L: TimelineLayout) {
  ctx.fillStyle = '#0B0D15';
  ctx.fillRect(0, 0, L.width, L.height);
}

export function drawTrackLanes(
  ctx: CanvasRenderingContext2D,
  L: TimelineLayout,
  tracks: Track[],
  selectedTrackId: string | null,
) {
  for (let i = 0; i < tracks.length; i++) {
    const y = trackToY(i, L);
    if (y + L.trackH < L.rulerH || y > L.height) continue;
    const track = tracks[i];

    // Lane background (alternating, tinted by kind)
    const base = i % 2 === 0 ? '#10121C' : '#0E1019';
    ctx.fillStyle = base;
    ctx.fillRect(L.headerW, y, L.width - L.headerW, L.trackH);

    // Subtle kind tint
    const color = TRACK_COLORS[track.kind] ?? '#4F8CFF';
    ctx.fillStyle = rgba(color, 0.03);
    ctx.fillRect(L.headerW, y, L.width - L.headerW, L.trackH);

    // Selected track highlight
    if (track.id === selectedTrackId) {
      ctx.fillStyle = rgba('#4F8CFF', 0.06);
      ctx.fillRect(L.headerW, y, L.width - L.headerW, L.trackH);
    }

    // Lane bottom border
    ctx.strokeStyle = 'rgba(70,70,79,0.25)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(L.headerW, y + L.trackH - 0.5);
    ctx.lineTo(L.width, y + L.trackH - 0.5);
    ctx.stroke();

    // Muted overlay
    if (track.muted) {
      ctx.fillStyle = 'rgba(11,13,21,0.55)';
      ctx.fillRect(L.headerW, y, L.width - L.headerW, L.trackH);
    }
  }
}

// ── Ruler ──────────────────────────────────────────────
const NICE_INTERVALS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];

export function drawRuler(
  ctx: CanvasRenderingContext2D,
  L: TimelineLayout,
  fps: number,
) {
  // Ruler background
  ctx.fillStyle = '#14161F';
  ctx.fillRect(0, 0, L.width, L.rulerH);
  ctx.strokeStyle = 'rgba(70,70,79,0.5)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, L.rulerH - 0.5);
  ctx.lineTo(L.width, L.rulerH - 0.5);
  ctx.stroke();

  // Choose major interval so labels are >= 70px apart
  let major = NICE_INTERVALS[NICE_INTERVALS.length - 1];
  for (const iv of NICE_INTERVALS) {
    if (iv * L.zoom >= 70) { major = iv; break; }
  }
  const minor = major / 5;

  const t0 = Math.max(0, (L.scrollX) / L.zoom - major);
  const t1 = (L.scrollX + L.width) / L.zoom + major;

  ctx.font = `400 9px ${MONO}`;
  ctx.textBaseline = 'top';

  // Minor ticks
  ctx.strokeStyle = 'rgba(70,70,79,0.55)';
  ctx.beginPath();
  for (let t = Math.floor(t0 / minor) * minor; t <= t1; t += minor) {
    const x = Math.round(timeToX(t, L)) + 0.5;
    if (x < L.headerW) continue;
    ctx.moveTo(x, L.rulerH - 6);
    ctx.lineTo(x, L.rulerH);
  }
  ctx.stroke();

  // Major ticks + labels
  ctx.strokeStyle = 'rgba(141,141,153,0.8)';
  ctx.fillStyle = '#8D8D99';
  const showFrames = useSettingsStore.getState().showFramesInRuler;
  for (let t = Math.floor(t0 / major) * major; t <= t1; t += major) {
    const x = Math.round(timeToX(t, L)) + 0.5;
    if (x < L.headerW) continue;
    ctx.beginPath();
    ctx.moveTo(x, L.rulerH - 12);
    ctx.lineTo(x, L.rulerH);
    ctx.stroke();
    const label = showFrames
      ? `${Math.round(t * fps)}`
      : major >= 60
        ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`
        : major >= 1
          ? `${t.toFixed(0)}s`
          : `${t.toFixed(1)}s`;
    ctx.fillText(label, x + 4, 6);
  }

  // Grid lines (when snapToGrid is enabled)
  const settings = useSettingsStore.getState();
  if (settings.snapToGrid && settings.snapGridSec > 0) {
    ctx.strokeStyle = 'rgba(79,139,237,0.18)';
    ctx.setLineDash([2, 6]);
    ctx.beginPath();
    for (let t = settings.snapGridSec; t <= t1; t += settings.snapGridSec) {
      const x = Math.round(timeToX(t, L)) + 0.5;
      if (x < L.headerW) continue;
      ctx.moveTo(x, L.rulerH);
      ctx.lineTo(x, L.height);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Loop region highlight
  const pst = usePreviewStore.getState();
  if (pst.isLooping && pst.loopRegion) {
    const lx0 = timeToX(pst.loopRegion.start, L);
    const lx1 = timeToX(pst.loopRegion.end, L);
    ctx.fillStyle = 'rgba(79,139,237,0.08)';
    ctx.fillRect(Math.max(L.headerW, lx0), 0, Math.min(L.width, lx1) - Math.max(L.headerW, lx0), L.rulerH);
  }
}

// ── Track headers ──────────────────────────────────────
export function drawTrackHeaders(
  ctx: CanvasRenderingContext2D,
  L: TimelineLayout,
  tracks: Track[],
  selectedTrackId: string | null,
  hoveredTrackId: string | null,
) {
  // Header column background
  ctx.fillStyle = '#12141E';
  ctx.fillRect(0, L.rulerH, L.headerW, L.height - L.rulerH);
  // Right border
  ctx.strokeStyle = 'rgba(70,70,79,0.5)';
  ctx.beginPath();
  ctx.moveTo(L.headerW - 0.5, L.rulerH);
  ctx.lineTo(L.headerW - 0.5, L.height);
  ctx.stroke();

  for (let i = 0; i < tracks.length; i++) {
    const y = trackToY(i, L);
    if (y + L.trackH < L.rulerH || y > L.height) continue;
    const track = tracks[i];
    const color = TRACK_COLORS[track.kind] ?? '#4F8CFF';

    if (track.id === selectedTrackId) {
      ctx.fillStyle = rgba('#4F8CFF', 0.10);
      ctx.fillRect(0, y, L.headerW, L.trackH);
    } else if (track.id === hoveredTrackId) {
      ctx.fillStyle = 'rgba(255,255,255,0.03)';
      ctx.fillRect(0, y, L.headerW, L.trackH);
    }

    // Kind color bar
    ctx.fillStyle = color;
    ctx.fillRect(0, y + 8, 3, L.trackH - 16);

    // Track name
    ctx.fillStyle = track.muted ? '#5A5A66' : '#C4C4D0';
    ctx.font = `500 10px ${SANS}`;
    ctx.textBaseline = 'middle';
    ctx.fillText(track.name, 12, y + L.trackH / 2 - 6, L.headerW - 40);

    // Kind label
    ctx.fillStyle = rgba(color, 0.8);
    ctx.font = `500 8px ${SANS}`;
    ctx.fillText(track.kind.toUpperCase(), 12, y + L.trackH / 2 + 8);

    // Lock / mute indicators
    let ix = L.headerW - 22;
    if (track.locked) {
      ctx.fillStyle = '#8D8D99';
      ctx.font = `400 10px ${SANS}`;
      ctx.fillText('🔒', ix, y + L.trackH / 2);
      ix -= 16;
    }
    if (track.muted) {
      ctx.fillStyle = '#8D8D99';
      ctx.font = `400 9px ${SANS}`;
      ctx.fillText('M', ix + 4, y + L.trackH / 2);
    }

    // Bottom border
    ctx.strokeStyle = 'rgba(70,70,79,0.25)';
    ctx.beginPath();
    ctx.moveTo(0, y + L.trackH - 0.5);
    ctx.lineTo(L.headerW, y + L.trackH - 0.5);
    ctx.stroke();
  }

  // Corner box (top-left)
  ctx.fillStyle = '#14161F';
  ctx.fillRect(0, 0, L.headerW, L.rulerH);
  ctx.strokeStyle = 'rgba(70,70,79,0.5)';
  ctx.strokeRect(0.5, 0.5, L.headerW - 1, L.rulerH - 1);
}

// ── Clips ──────────────────────────────────────────────
/** Deterministic pseudo-random for stable waveforms */
function seededRand(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}
function hashStr(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  return Math.abs(h) || 1;
}

export function drawClip(
  ctx: CanvasRenderingContext2D,
  L: TimelineLayout,
  track: Track,
  trackIndex: number,
  clip: Clip,
  opts: {
    selected: boolean;
    hovered: boolean;
    isDragGhost?: boolean;
    ghostDeltaTime?: number;
    ghostDeltaTrack?: number;
  },
) {
  const baseColor = clip.label_color || (TRACK_COLORS[track.kind] ?? '#4F8CFF');
  const color = baseColor;
  const dt = opts.ghostDeltaTime ?? 0;
  const dtr = opts.ghostDeltaTrack ?? 0;

  const x = timeToX(clip.start_sec + dt, L);
  const w = clip.duration_sec * L.zoom;
  const y = trackToY(trackIndex + dtr, L) + 3;
  const h = L.trackH - 6;

  // Cull offscreen
  if (x + w < L.headerW || x > L.width) return;

  // Clip body
  const dimmed = clip.enabled === false ? 0.35 : 1;
  const bodyAlpha = (opts.isDragGhost ? 0.55 : 1) * dimmed;
  const grad = ctx.createLinearGradient(0, y, 0, y + h);
  grad.addColorStop(0, rgba(lighten(color, 0.12), 0.95 * bodyAlpha));
  grad.addColorStop(0.12, rgba(color, 0.88 * bodyAlpha));
  grad.addColorStop(1, rgba(shade(color, 0.35), 0.92 * bodyAlpha));
  ctx.fillStyle = grad;
  roundRect(ctx, x, y, w, h, 5);
  ctx.fill();

  // Inner content by kind
  ctx.save();
  roundRect(ctx, x, y, w, h, 5);
  ctx.clip();

  if (track.kind === 'audio' || track.kind === 'waveform') {
    drawWaveform(ctx, clip, x, y, w, h);
  } else if (track.kind === 'video' || track.kind === 'image') {
    drawVideoContent(ctx, clip, x, y, w, h);
  } else if (track.kind === 'text' || track.kind === 'caption') {
    drawTextLines(ctx, clip, x, y, w, h);
  }

  // Top highlight
  ctx.fillStyle = `rgba(255,255,255,${0.10 * bodyAlpha})`;
  ctx.fillRect(x, y, w, 1.5);
  ctx.restore();

  // Label
  if (w > 34) {
    ctx.fillStyle = `rgba(255,255,255,${0.92 * bodyAlpha})`;
    ctx.font = `500 9px ${SANS}`;
    ctx.textBaseline = 'top';
    const label = clipLabel(clip, track.kind);
    ctx.fillText(label, x + 6, y + 5, w - 12);
    // Duration sublabel
    if (w > 60) {
      ctx.fillStyle = `rgba(255,255,255,${0.55 * bodyAlpha})`;
      ctx.font = `400 8px ${MONO}`;
      ctx.fillText(`${clip.duration_sec.toFixed(1)}s`, x + 6, y + 17, w - 12);
    }
  }

  // Border
  if (opts.selected) {
    ctx.strokeStyle = '#FFFFFF';
    ctx.lineWidth = 1.5;
    roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, 5);
    ctx.stroke();
    // Glow
    ctx.strokeStyle = rgba(color, 0.5);
    ctx.lineWidth = 3;
    roundRect(ctx, x - 1, y - 1, w + 2, h + 2, 6);
    ctx.stroke();
  } else {
    ctx.strokeStyle = rgba(lighten(color, 0.25), (opts.hovered ? 0.9 : 0.45) * bodyAlpha);
    ctx.lineWidth = 1;
    roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, 5);
    ctx.stroke();
  }

  // Hover brighten
  if (opts.hovered && !opts.selected) {
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    roundRect(ctx, x, y, w, h, 5);
    ctx.fill();
  }

  // Trim handles when selected
  if (opts.selected && w > 20) {
    ctx.fillStyle = '#FFFFFF';
    roundRect(ctx, x + 1, y + h / 2 - 8, 3, 16, 1.5);
    ctx.fill();
    roundRect(ctx, x + w - 4, y + h / 2 - 8, 3, 16, 1.5);
    ctx.fill();
  }

  // Keyframe dots
  if (clip.keyframes.length > 0) {
    for (const kf of clip.keyframes) {
      const kx = x + kf.time * w;
      const ky = y + h - 6;
      ctx.fillStyle = '#FBBF24';
      ctx.beginPath();
      ctx.moveTo(kx, ky - 3);
      ctx.lineTo(kx + 3, ky);
      ctx.lineTo(kx, ky + 3);
      ctx.lineTo(kx - 3, ky);
      ctx.closePath();
      ctx.fill();
    }
  }

  // M11: 转场可见性 — 在片段首/尾绘制转场徽标（双三角形 + 转场时长）
  drawTransitionBadges(ctx, clip, x, y, w, h, bodyAlpha);
}

const TRANSITION_LABEL: Record<string, string> = {
  fade: '淡入淡出', dissolve: '溶解', glitch: '故障', pixel_dissolve: '像素溶解',
  slide: '滑动', wipe: '划像', hard_cut: '硬切',
};

/** M11: 在片段边缘绘制转场指示（进/出转场）。 */
function drawTransitionBadges(
  ctx: CanvasRenderingContext2D,
  clip: Clip,
  x: number, y: number, w: number, h: number,
  bodyAlpha: number,
) {
  const dur = clip.transition_duration_sec ?? 0.5;
  const drawBadge = (edgeX: number, type: string, side: 'in' | 'out') => {
    const bw = Math.min(14, Math.max(6, w / 3));
    const bx = side === 'in' ? edgeX : edgeX - bw;
    if (bx < 0 || bx + bw > x + w + 1) return;
    ctx.save();
    ctx.globalAlpha = 0.9 * bodyAlpha;
    // 深色底条
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    ctx.fillRect(bx, y + h / 2 - 7, bw, 14);
    // 双三角形（◀▶ 合并形）
    ctx.fillStyle = '#FFD700';
    ctx.beginPath();
    if (side === 'in') {
      ctx.moveTo(bx + 3, y + h / 2);
      ctx.lineTo(bx + bw - 3, y + h / 2 - 4);
      ctx.lineTo(bx + bw - 3, y + h / 2 + 4);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.moveTo(bx + bw - 3, y + h / 2);
      ctx.lineTo(bx + 3, y + h / 2 - 4);
      ctx.lineTo(bx + 3, y + h / 2 + 4);
      ctx.closePath();
      ctx.fill();
    }
    // 转场时长标签（宽度足够时）
    const label = TRANSITION_LABEL[type] ?? type;
    if (w > 70) {
      ctx.font = `400 8px ${MONO}`;
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'center';
      ctx.fillText(`${dur.toFixed(1)}s`, bx + bw / 2, y + h / 2 - 9);
      ctx.fillText(label, bx + bw / 2, y + h / 2 + 9);
      ctx.textAlign = 'left';
    }
    ctx.restore();
  };

  if (clip.transition_in && clip.transition_in !== 'hard_cut') drawBadge(x, clip.transition_in, 'in');
  if (clip.transition_out && clip.transition_out !== 'hard_cut') drawBadge(x + w, clip.transition_out, 'out');
}

function clipLabel(clip: Clip, kind: ClipKind): string {
  if ((kind === 'text' || kind === 'caption') && clip.text) return clip.text;
  if (clip.asset_id) return clip.asset_id;
  if (clip.metadata && typeof clip.metadata.title === 'string') return clip.metadata.title as string;
  return kind;
}

function drawWaveform(
  ctx: CanvasRenderingContext2D, clip: Clip,
  x: number, y: number, w: number, h: number,
) {
  const barW = 2.5;
  const gap = 1.5;
  const mid = y + h / 2 + 4;
  const maxAmp = h / 2 - 8;

  // Try real decoded peaks first
  const peaks = mediaManager.getCachedWaveform(clip.asset_id);
  if (peaks && peaks.length > 0) {
    // Kick off decode if this was a placeholder empty array
    ctx.fillStyle = 'rgba(255,255,255,0.62)';
    const n = peaks.length;
    const usable = Math.max(1, Math.floor(w / (barW + gap)));
    for (let i = 0; i < usable; i++) {
      // Map visible bar index -> peak index, honoring source offset/speed window
      const frac = usable > 1 ? i / (usable - 1) : 0;
      const pIdx = Math.min(n - 1, Math.floor(frac * n));
      const amp = Math.max(1.5, peaks[pIdx] * maxAmp);
      ctx.fillRect(x + 4 + i * (barW + gap), mid - amp, barW, amp * 2);
    }
    return;
  }

  // Trigger async decode for next frame, fall back to pseudo waveform now
  mediaManager.ensureWaveform(clip.asset_id);
  const rand = seededRand(hashStr(clip.id));
  ctx.fillStyle = 'rgba(255,255,255,0.45)';
  for (let bx = x + 4; bx < x + w - 3; bx += barW + gap) {
    const amp = (0.15 + rand() * 0.75) * maxAmp;
    ctx.fillRect(bx, mid - amp, barW, amp * 2);
  }
}

/** Image cache for thumbnail dataURLs (so drawImage can use them). */
const thumbImageCache = new Map<string, HTMLImageElement>();
function getThumbImage(dataUrl: string): HTMLImageElement {
  let img = thumbImageCache.get(dataUrl);
  if (!img) {
    img = new Image();
    img.src = dataUrl;
    thumbImageCache.set(dataUrl, img);
  }
  return img;
}

/**
 * Draw video clip content: real thumbnail frames tiled across the clip when
 * decoded, filmstrip placeholder otherwise (kicks off async frame capture).
 */
function drawVideoContent(ctx: CanvasRenderingContext2D, clip: Clip, x: number, y: number, w: number, h: number) {
  const tileW = 56;
  const contentTop = y + 16;
  const contentH = h - 26;
  let anyReal = false;

  // Tile thumbnails across the clip
  for (let tx = x; tx < x + w; tx += tileW) {
    const tw = Math.min(tileW, x + w - tx);
    if (tw <= 0) break;
    const frac = w > 0 ? (tx - x + tw / 2) / w : 0;
    const clipLocalTime = frac * clip.duration_sec;
    const sourceT = clip.source_offset_sec + clipLocalTime * clip.speed;

    const dataUrl = mediaManager.getCachedThumbnailAt(clip.asset_id, sourceT);
    if (dataUrl) {
      const img = getThumbImage(dataUrl);
      if (img.complete && img.naturalWidth > 0) {
        // cover-fit the frame into the tile
        const sa = img.naturalWidth / img.naturalHeight;
        const da = tw / contentH;
        let sw = img.naturalWidth, sh = img.naturalHeight, sx = 0, sy = 0;
        if (sa > da) { sw = img.naturalHeight * da; sx = (img.naturalWidth - sw) / 2; }
        else { sh = img.naturalWidth / da; sy = (img.naturalHeight - sh) / 2; }
        ctx.save();
        roundRect(ctx, tx, contentTop, tw, contentH, 2);
        ctx.clip();
        ctx.globalAlpha = 0.9;
        ctx.drawImage(img, sx, sy, sw, sh, tx, contentTop, tw, contentH);
        ctx.restore();
        anyReal = true;
        continue;
      }
    }

    // Placeholder for this tile + kick off capture
    mediaManager.ensureThumbnail(clip.asset_id, sourceT);
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    ctx.fillRect(tx, contentTop, tw, contentH);
  }

  // Frame dividers between tiles
  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1;
  for (let fx = x + tileW; fx < x + w - 2; fx += tileW) {
    ctx.beginPath();
    ctx.moveTo(fx + 0.5, contentTop);
    ctx.lineTo(fx + 0.5, contentTop + contentH);
    ctx.stroke();
  }

  // Sprocket holes along bottom (film identity)
  ctx.fillStyle = anyReal ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.28)';
  const holeY = y + h - 8;
  for (let hx = x + 5; hx < x + w - 6; hx += 14) {
    roundRect(ctx, hx, holeY, 7, 4.5, 1);
    ctx.fill();
  }
}

function drawTextLines(ctx: CanvasRenderingContext2D, clip: Clip, x: number, y: number, w: number, h: number) {
  ctx.fillStyle = 'rgba(255,255,255,0.35)';
  const lineY = y + h / 2 + 6;
  const maxW = Math.min(w - 16, 90);
  roundRect(ctx, x + 6, lineY, maxW, 3, 1.5);
  ctx.fill();
  roundRect(ctx, x + 6, lineY + 7, maxW * 0.6, 3, 1.5);
  ctx.fill();
}

// ── Playhead ───────────────────────────────────────────
export function drawPlayhead(ctx: CanvasRenderingContext2D, L: TimelineLayout, timeSec: number) {
  const x = Math.round(timeToX(timeSec, L)) + 0.5;
  if (x < L.headerW - 1 || x > L.width + 1) return;

  // Glow line
  ctx.strokeStyle = 'rgba(255,68,68,0.25)';
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(x, L.rulerH);
  ctx.lineTo(x, L.height);
  ctx.stroke();

  // Main line
  ctx.strokeStyle = '#FF4444';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, L.height);
  ctx.stroke();

  // Handle cap
  ctx.fillStyle = '#FF4444';
  ctx.beginPath();
  ctx.moveTo(x - 5, 0);
  ctx.lineTo(x + 5, 0);
  ctx.lineTo(x + 5, 10);
  ctx.lineTo(x, 16);
  ctx.lineTo(x - 5, 10);
  ctx.closePath();
  ctx.fill();

  // Timecode on cap
  ctx.fillStyle = '#FFFFFF';
  ctx.font = `500 7px ${MONO}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(timeSec.toFixed(1), x, 2);
  ctx.textAlign = 'left';
}

// ── Markers ────────────────────────────────────────────
export function drawMarkers(ctx: CanvasRenderingContext2D, L: TimelineLayout, markers: Marker[]) {
  for (const m of markers) {
    const x = Math.round(timeToX(m.time, L)) + 0.5;
    if (x < L.headerW || x > L.width) continue;
    ctx.fillStyle = '#FFD700';
    ctx.beginPath();
    ctx.moveTo(x, L.rulerH - 10);
    ctx.lineTo(x + 4, L.rulerH - 5);
    ctx.lineTo(x, L.rulerH);
    ctx.lineTo(x - 4, L.rulerH - 5);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,215,0,0.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, L.rulerH);
    ctx.lineTo(x, L.height);
    ctx.stroke();
    if (m.name && m.name.trim()) {
      ctx.fillStyle = '#FFD700';
      ctx.font = `400 9px ${SANS}`;
      ctx.textBaseline = 'bottom';
      ctx.fillText(m.name, x + 6, L.rulerH - 6);
    }
  }
}

// ── Snap guide ─────────────────────────────────────────
export function drawSnapGuide(ctx: CanvasRenderingContext2D, L: TimelineLayout, x: number) {
  ctx.strokeStyle = '#00E5FF';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(Math.round(x) + 0.5, L.rulerH);
  ctx.lineTo(Math.round(x) + 0.5, L.height);
  ctx.stroke();
  ctx.setLineDash([]);
}

// ── Marquee selection ──────────────────────────────────
export function drawMarquee(
  ctx: CanvasRenderingContext2D,
  rect: { x0: number; y0: number; x1: number; y1: number },
) {
  const x = Math.min(rect.x0, rect.x1);
  const y = Math.min(rect.y0, rect.y1);
  const w = Math.abs(rect.x1 - rect.x0);
  const h = Math.abs(rect.y1 - rect.y0);
  ctx.fillStyle = 'rgba(79,140,255,0.12)';
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = 'rgba(79,140,255,0.7)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.strokeRect(x + 0.5, y + 0.5, w, h);
  ctx.setLineDash([]);
}

// ── Empty state ────────────────────────────────────────
export function drawEmptyState(ctx: CanvasRenderingContext2D, L: TimelineLayout) {
  ctx.fillStyle = '#5A5A66';
  ctx.font = `400 12px ${SANS}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const cx = L.headerW + (L.width - L.headerW) / 2;
  const cy = L.rulerH + (L.height - L.rulerH) / 2;
  ctx.fillText('时间轴为空 — 从素材面板拖入素材，或让 Agent 生成初稿', cx, cy - 10);
  ctx.font = `400 10px ${SANS}`;
  ctx.fillStyle = '#46464F';
  ctx.fillText('点击下方「添加轨道」开始编辑', cx, cy + 10);
  ctx.textAlign = 'left';
}

// ── Horizontal scrollbar ───────────────────────────────
export function drawHorizontalScrollbar(
  ctx: CanvasRenderingContext2D,
  L: TimelineLayout,
  contentW: number,
  state: 'idle' | 'hover' | 'drag',
) {
  const g = scrollbarGeom(L, contentW);
  if (g.trackW <= 0) return;

  // Track bed + top hairline separator
  ctx.fillStyle = 'rgba(255,255,255,0.035)';
  ctx.fillRect(g.trackX, g.y, g.trackW, g.h);
  ctx.fillStyle = 'rgba(255,255,255,0.07)';
  ctx.fillRect(g.trackX, g.y, g.trackW, 1);

  // Thumb (fills the track when all content is visible)
  const inset = 2;
  const ty = g.y + inset;
  const th = g.h - inset * 2;
  const tw = Math.max(8, g.thumbW);

  const grad = ctx.createLinearGradient(0, ty, 0, ty + th);
  if (state === 'drag') {
    grad.addColorStop(0, 'rgba(110,155,255,0.95)');
    grad.addColorStop(1, 'rgba(79,140,255,0.95)');
  } else if (state === 'hover') {
    grad.addColorStop(0, 'rgba(150,165,205,0.85)');
    grad.addColorStop(1, 'rgba(120,135,175,0.85)');
  } else {
    grad.addColorStop(0, 'rgba(125,135,165,0.55)');
    grad.addColorStop(1, 'rgba(100,110,140,0.55)');
  }
  ctx.fillStyle = grad;
  roundRect(ctx, g.thumbX, ty, tw, th, th / 2);
  ctx.fill();
}
