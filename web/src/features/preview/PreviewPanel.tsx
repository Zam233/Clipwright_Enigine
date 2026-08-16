import { useCallback, useEffect, useRef, useState } from 'react';
import { useTimelineStore } from '@/stores/timelineStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useHistoryStore } from '@/stores/historyStore';
import { TRACK_COLORS } from '@/types/timeline';
import type { Clip, Track } from '@/types/timeline';
import { captionBaselineY, captionFontSize } from './captionLayout';
import { orderTracksForComposite } from './compositeOrder';
import { computePreviewFrameRect, xToScrubTime } from './frameGeometry';
import type { FrameRect } from './frameGeometry';
import { formatTimecode, clamp } from '@/lib/utils';
import { mediaManager } from '@/services/media/mediaManager';
import { interpolateProperties } from '@/features/timeline/engine/easing';
import { Maximize, Shield, Volume2, VolumeX, ZoomIn, ZoomOut, Camera, Repeat, Play, Pause } from 'lucide-react';
import { Tooltip, Slider } from '@/components/ui';
import { applyMasterVolume } from './volume';

/**
 * PreviewPanel — Canvas compositor that renders the timeline at the playhead.
 * Drives playback via requestAnimationFrame and syncs duration with the timeline.
 */
export function PreviewPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Drag-scrub state: start coords + dragging flag + rAF-merged target time.
  // 拖拽刷洗状态：起点坐标 + 拖拽标志 + rAF 合并的目标时间。
  const scrubRef = useRef({ startX: 0, startY: 0, dragging: false, raf: 0, targetTime: -1 });

  const timeline = useTimelineStore((s) => s.timeline);
  const currentTimeSec = usePreviewStore((s) => s.currentTimeSec);
  const isPlaying = usePreviewStore((s) => s.isPlaying);
  const showSafeArea = usePreviewStore((s) => s.showSafeArea);
  const isMuted = usePreviewStore((s) => s.isMuted);
  const toggleMute = usePreviewStore((s) => s.toggleMute);
  const toggleSafeArea = usePreviewStore((s) => s.toggleSafeArea);
  const setFullscreen = usePreviewStore((s) => s.setFullscreen);
  const zoomLevel = usePreviewStore((s) => s.zoomLevel);
  const setZoomLevel = usePreviewStore((s) => s.setZoomLevel);
  const playbackSpeed = usePreviewStore((s) => s.playbackSpeed);
  const setPlaybackSpeed = usePreviewStore((s) => s.setPlaybackSpeed);
  const isLooping = usePreviewStore((s) => s.isLooping);
  const toggleLoop = usePreviewStore((s) => s.toggleLoop);
  const volume = usePreviewStore((s) => s.volume);
  const setVolume = usePreviewStore((s) => s.setVolume);

  // C1: 画布双击编辑文字 — 命中 text/caption 片段时打开内联编辑
  const [editingText, setEditingText] = useState<{ clipId: string; text: string; x: number; y: number } | null>(null);
  const editTextRef = useRef<HTMLTextAreaElement>(null);

  const handleDoubleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const rect = wrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const pst = usePreviewStore.getState();
    const tl = useTimelineStore.getState().timeline;
    const t = pst.currentTimeSec;
    const fr = computePreviewFrameRect(rect.width, rect.height, tl.width, tl.height, pst.zoomLevel);
    // 点击必须在帧内
    if (px < fr.fx || px > fr.fx + fr.fw || py < fr.fy || py > fr.fy + fr.fh) return;
    const hit = hitTestTextClipForEdit(px, py, fr, tl.tracks, t);
    if (hit) setEditingText({ clipId: hit.clip.id, text: hit.clip.text ?? '', x: hit.x, y: hit.y });
  }, []);

  const commitTextEdit = useCallback(() => {
    setEditingText((cur) => {
      if (cur) {
        useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'edit-text');
        useTimelineStore.getState().updateClip(cur.clipId, { text: cur.text });
      }
      return null;
    });
  }, []);

  useEffect(() => {
    if (editingText) {
      // 聚焦并选中全部文本，方便直接覆盖
      const el = editTextRef.current;
      if (el) { el.focus(); el.select(); }
    }
  }, [editingText]);

  // Sync duration from timeline
  useEffect(() => {
    usePreviewStore.getState().setDuration(timeline.duration_sec);
    usePreviewStore.getState().setFps(timeline.fps);
  }, [timeline.duration_sec, timeline.fps]);

  // Audio sync: play/pause/seek audio clips' media elements with the playhead.
  // 播放期间节流至 ~10fps，避免每帧遍历全部 clip（seek 阈值本身为 0.25s）
  const lastAudioSyncRef = useRef(0);
  const audioStateRef = useRef({ playing: false, muted: false, tl: timeline as unknown });
  useEffect(() => {
    const prev = audioStateRef.current;
    const stateChanged = prev.playing !== isPlaying || prev.muted !== isMuted;
    audioStateRef.current = { playing: isPlaying, muted: isMuted, tl: timeline };
    const now = performance.now();
    if (!stateChanged && now - lastAudioSyncRef.current < 100) return;
    lastAudioSyncRef.current = now;

    const t = currentTimeSec;
    const playing = isPlaying;
    const muted = isMuted;
    for (const track of timeline.tracks) {
      if (track.kind !== 'audio' && track.kind !== 'waveform') continue;
      for (const clip of track.clips) {
        if (clip.enabled === false) continue; // 禁用的片段不发声（与画面渲染一致）
        const entry = mediaManager.get(clip.asset_id);
        const el = entry?.audioEl ?? entry?.videoEl;
        if (!el) continue;
        const inClip = t >= clip.start_sec && t < clip.start_sec + clip.duration_sec;
        // Account for clip speed so audio stays in sync with sped-up/slowed video
        const localT = (t - clip.start_sec) * clip.speed + clip.source_offset_sec;
        el.volume = applyMasterVolume(clip.volume, volume);
        el.muted = muted || track.muted;
        if (Math.abs(el.playbackRate - clip.speed) > 0.01) {
          try { el.playbackRate = clip.speed; } catch { /* not supported */ }
        }
          if (inClip && playing) {
          if (Math.abs(el.currentTime - localT) > 0.25) { try { el.currentTime = localT; } catch { /* seek not available */ } }
          if (el.paused) {
            mediaManager.attachAnalyser(clip.asset_id);
            el.play().catch(() => {});
          }
        } else {
          if (!el.paused) el.pause();
        }
      }
    }
  }, [currentTimeSec, isPlaying, isMuted, timeline, volume]);

  // Playback loop
  useEffect(() => {
    if (!isPlaying) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 1 / 15);
      last = now;
      const st = usePreviewStore.getState();
      const speed = st.shuttleSpeed !== 0 ? st.shuttleSpeed * Math.abs(st.playbackSpeed) : st.playbackSpeed;
      let next = st.currentTimeSec + dt * speed;
      const dur = useTimelineStore.getState().timeline.duration_sec;
      const region = st.loopRegion;
      const looping = st.isLooping;

      if (looping) {
        // Loop region (or the whole timeline if no In/Out markers set)
        // 钳位到时间线范围内：时间线被修剪变短后，标记可能越界
        const lo = Math.min(Math.max(region ? region.start : 0, 0), dur);
        const hi = Math.min(Math.max(region ? region.end : dur, lo + 0.01), dur);
        const span = hi - lo;
        if (span > 0) {
          if (next >= hi) next = lo + ((next - hi) % span); // carry overshoot
          else if (next < lo) next = hi - ((lo - next) % span);
        }
      } else if (next >= dur) {
        next = dur;
        st.setPlaying(false);
      } else if (next < 0) {
        next = 0;
        st.setPlaying(false);
      }
      st.setCurrentTime(next);
      if (usePreviewStore.getState().isPlaying) {
        raf = requestAnimationFrame(tick);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying]);

  // Pause all media when the preview unmounts (avoid audio playing after leaving the editor)
  useEffect(() => () => {
    cancelAnimationFrame(scrubRef.current.raf);
    mediaManager.pauseAll();
  }, []);

  // Canvas pointer scrub: drag horizontally to seek the playhead, click to toggle play.
  // 画布指针刷洗：水平拖拽移动播放头，单击切换播放/暂停。
  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* capture unavailable */ }
    const s = scrubRef.current;
    s.startX = e.clientX;
    s.startY = e.clientY;
    s.dragging = false;
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const s = scrubRef.current;
    const d = Math.abs(e.clientX - s.startX) + Math.abs(e.clientY - s.startY);
    if (d > 5) {
      s.dragging = true;
      if (usePreviewStore.getState().isPlaying) usePreviewStore.getState().setPlaying(false);
    }
    if (!s.dragging) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const tl = useTimelineStore.getState().timeline;
    const zoom = usePreviewStore.getState().zoomLevel;
    const frame = computePreviewFrameRect(rect.width, rect.height, tl.width, tl.height, zoom);
    s.targetTime = xToScrubTime(e.clientX - rect.left, frame, tl.duration_sec);
    if (s.raf === 0) {
      s.raf = requestAnimationFrame(() => {
        scrubRef.current.raf = 0;
        usePreviewStore.getState().setCurrentTime(scrubRef.current.targetTime);
      });
    }
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const s = scrubRef.current;
    if (s.raf !== 0) {
      cancelAnimationFrame(s.raf);
      s.raf = 0;
      usePreviewStore.getState().setCurrentTime(s.targetTime);
    }
    if (!s.dragging) {
      usePreviewStore.getState().togglePlay();
    }
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    }
    s.dragging = false;
  }, []);

  const handlePointerCancel = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const s = scrubRef.current;
    if (s.raf !== 0) {
      cancelAnimationFrame(s.raf);
      s.raf = 0;
    }
    s.dragging = false;
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) {
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    }
  }, []);

  // Keep fullscreen state in sync with the browser (e.g. user exits via Escape)
  useEffect(() => {
    const onFsChange = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, [setFullscreen]);

  // Composite render — 持久 RAF 循环 + 单次 ResizeObserver。
  // 不依赖 currentTimeSec，避免播放期间每帧重建 observer；仅在状态变化时重绘。
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let raf = 0;
    let lastW = 0;
    let lastH = 0;
    const last = { t: -1, tl: null as unknown, safe: false, zoom: -1, mv: -1 };

    // Re-render when media state changes (e.g. an asset errors while loading) —
    // otherwise the failed clip would stay a silent black frame until the playhead moves.
    let mediaVersion = 0;
    const unsubMedia = mediaManager.onChange(() => { mediaVersion++; });

    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = wrap.getBoundingClientRect();
      const W = rect.width;
      const H = rect.height;
      if (W !== lastW || H !== lastH) {
        lastW = W;
        lastH = H;
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        canvas.style.width = `${W}px`;
        canvas.style.height = `${H}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const pst = usePreviewStore.getState();
      const tl = useTimelineStore.getState().timeline;
      const t = pst.currentTimeSec;
      const showSafe = pst.showSafeArea;

      // Letterbox background
      ctx.fillStyle = '#08090F';
      ctx.fillRect(0, 0, W, H);

      // Fit video frame into panel (16:9 by default), scaled by preview zoom
      const zoom = pst.zoomLevel;
      const aspect = tl.height > 0 ? tl.width / tl.height : 16 / 9;
      let fw = (W - 32) * zoom;
      let fh = fw / aspect;
      if (fh > (H - 32) * zoom) {
        fh = (H - 32) * zoom;
        fw = fh * aspect;
      }
      const fx = (W - fw) / 2;
      const fy = (H - fh) / 2;

      // Frame background
      ctx.fillStyle = '#0E101A';
      ctx.fillRect(fx, fy, fw, fh);

      // Composite order: lowest track index drawn first (bottom layer), highest index last (top layer) — matches backend render.py is_overlay = track.index > 0.
      // 合成顺序：轨道 index 升序，低 index 先画（底层），高 index 最后（顶层）。
      const sorted = orderTracksForComposite(tl.tracks);
      for (const track of sorted) {
        if (track.hidden) continue; // M7: 隐藏轨道不参与预览合成
        if (track.muted && (track.kind === 'audio' || track.kind === 'waveform')) continue;
        for (const clip of track.clips) {
          if (t < clip.start_sec || t >= clip.start_sec + clip.duration_sec) continue;
          if (clip.enabled === false) continue;
          // C3: 嵌套序列 — 递归合成子时间线（深度上限 4，防循环）
          if (clip.nested_timeline) {
            drawNestedTimeline(ctx, clip, track, fx, fy, fw, fh, t, 0);
            continue;
          }
          drawClipToPreview(ctx, clip, track, fx, fy, fw, fh, t);
        }
      }

      // Empty frame hint
      if (tl.tracks.length === 0) {
        ctx.fillStyle = '#46464F';
        ctx.font = "400 13px 'Inter','Noto Sans SC',sans-serif";
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('预览窗口 — 添加素材后在此实时预览', W / 2, H / 2);
        ctx.textAlign = 'left';
      }

      // Safe area overlay
      if (showSafe) {
        ctx.strokeStyle = 'rgba(255,68,68,0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([5, 4]);
        // Action safe (90%)
        ctx.strokeRect(fx + fw * 0.05, fy + fh * 0.05, fw * 0.9, fh * 0.9);
        // Title safe (80%)
        ctx.strokeStyle = 'rgba(0,229,255,0.5)';
        ctx.strokeRect(fx + fw * 0.1, fy + fh * 0.1, fw * 0.8, fh * 0.8);
        ctx.setLineDash([]);
      }

      // Frame border
      ctx.strokeStyle = 'rgba(141,141,153,0.3)';
      ctx.lineWidth = 1;
      ctx.strokeRect(fx + 0.5, fy + 0.5, fw - 1, fh - 1);
    };

    const loop = () => {
      const pst = usePreviewStore.getState();
      const tl = useTimelineStore.getState().timeline;
      const t = pst.currentTimeSec;
      const safe = pst.showSafeArea;
      const zoom = pst.zoomLevel;
      if (t !== last.t || tl !== last.tl || safe !== last.safe || zoom !== last.zoom || mediaVersion !== last.mv) {
        last.t = t;
        last.tl = tl;
        last.safe = safe;
        last.zoom = zoom;
        last.mv = mediaVersion;
        draw();
      }
      raf = requestAnimationFrame(loop);
    };
    draw();
    raf = requestAnimationFrame(loop);

    const ro = new ResizeObserver(() => draw());
    ro.observe(wrap);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      unsubMedia();
    };
  }, []);

  return (
    <div className="flex flex-col h-full bg-surface-dim">
      {/* Preview header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-outline-variant/30 shrink-0">
        <span className="text-label font-medium text-on-surface-variant uppercase tracking-wide">
          节目监视器
        </span>
        <div className="flex items-center gap-1">
          {/* preview zoom */}
          <Tooltip content="缩小预览">
            <button onClick={() => setZoomLevel(Math.max(0.25, zoomLevel - 0.25))}
              className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <button onClick={() => setZoomLevel(1)} title="重置为 100%"
            className="px-1.5 py-0.5 rounded-cw-xs font-mono text-caption text-on-surface-variant hover:text-primary hover:bg-primary/10 transition-colors cursor-pointer min-w-[42px] text-center">
            {Math.round(zoomLevel * 100)}%
          </button>
          <Tooltip content="放大预览">
            <button onClick={() => setZoomLevel(Math.min(4, zoomLevel + 0.25))}
              className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <div className="w-px h-4 bg-outline-variant/40 mx-0.5" />
          <Tooltip content="播放速度">
            <button onClick={() => {
              const speeds = [0.5, 1, 1.5, 2];
              setPlaybackSpeed(nextPlaybackSpeed(playbackSpeed, speeds));
            }}
              className="px-1.5 py-0.5 rounded-cw-xs font-mono text-caption text-on-surface-variant hover:text-primary hover:bg-primary/10 transition-colors cursor-pointer min-w-[38px] text-center">
              {playbackSpeed}×
            </button>
          </Tooltip>
          <Tooltip content={isLooping ? '关闭循环 (/)' : '开启循环 (/)'}>
            <button onClick={toggleLoop}
              className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${isLooping ? 'text-track-video bg-track-video/10' : 'text-on-surface-variant hover:text-on-surface'}`}>
              <Repeat className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <div className="w-px h-4 bg-outline-variant/40 mx-0.5" />
          <Slider label="音量" min={0} max={1} step={0.05} value={volume} onChange={setVolume} className="min-w-[80px] shrink-0" />
          <Tooltip content={isMuted ? '取消静音' : '静音'}>
            <button onClick={toggleMute}
              className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              {isMuted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
            </button>
          </Tooltip>
          <Tooltip content="安全框">
            <button onClick={toggleSafeArea}
              className={`p-1.5 rounded-cw-xs transition-colors cursor-pointer ${showSafeArea ? 'text-snap-guide bg-snap-guide/10' : 'text-on-surface-variant hover:text-on-surface'}`}>
              <Shield className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="全屏">
            <button onClick={() => {
              // 全屏请求可能被浏览器拒绝：成功后才置位，避免 UI 状态与实际不一致
              wrapRef.current?.requestFullscreen?.()
                .then(() => setFullscreen(true))
                .catch(() => setFullscreen(false));
            }}
              className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              <Maximize className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="导出当前帧 (PNG)">
            <button onClick={() => {
              const canvas = canvasRef.current;
              if (!canvas) return;
              const link = document.createElement('a');
              link.download = `frame_${Math.floor(currentTimeSec * 1000)}.png`;
              link.href = canvas.toDataURL('image/png');
              link.click();
            }}
              className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
              <Camera className="w-3.5 h-3.5" />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Canvas viewport */}
      <div ref={wrapRef} className="flex-1 relative overflow-hidden min-h-0 group/preview">
        <canvas
          ref={canvasRef}
          className="absolute inset-0 block"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onDoubleClick={handleDoubleClick}
        />
        {/* C1: 画布内联文字编辑 */}
        {editingText && (
          <div className="absolute z-20" style={{ left: editingText.x, top: editingText.y, transform: 'translate(-50%, -50%)' }}>
            <textarea
              ref={editTextRef}
              value={editingText.text}
              onChange={(e) => setEditingText({ ...editingText, text: e.target.value })}
              onBlur={commitTextEdit}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitTextEdit(); }
                if (e.key === 'Escape') setEditingText(null);
              }}
              rows={2}
              className="w-64 max-w-[80vw] bg-black/85 text-white text-center rounded-cw-xs
                px-3 py-2 text-body outline-none border-2 border-primary/70 resize-none shadow-2xl"
              aria-label="编辑文字"
            />
          </div>
        )}
        {/* Centered play/pause overlay — visible when paused or on hover */}
        <button
          aria-label={isPlaying ? '暂停' : '播放'}
          className={`absolute inset-0 m-auto w-12 h-12 rounded-cw-full flex items-center justify-center
            bg-black/50 text-white backdrop-blur-sm transition-opacity duration-short3 pointer-events-none
            ${isPlaying ? 'opacity-0 group-hover/preview:opacity-100' : 'opacity-35 group-hover/preview:opacity-100'}`}
        >
          {isPlaying ? <Pause className="w-6 h-6" /> : <Play className="w-6 h-6 ml-0.5" />}
        </button>
      </div>

      {/* Resolution / info bar */}
      <div className="flex items-center justify-between px-3 py-1 border-t border-outline-variant/30 text-caption text-on-surface-variant font-mono shrink-0">
        <div className="flex items-center gap-2">
          <span>{timeline.width}×{timeline.height} · {timeline.fps}fps</span>
          <AudioLevelMeter />
        </div>
        <span>{formatTimecode(currentTimeSec, timeline.fps)}</span>
      </div>
    </div>
  );
}

/** Draw a single clip into the preview frame (placeholder compositing). */
/**
 * M11: 转场可见性 — 在进/出转场窗口内调制透明度，让淡入淡出/溶解类转场在预览中可见。
 * - transition_in：片段开头 dur 秒内透明度 0→1（前一片段叠化进来）
 * - transition_out：片段结尾 dur 秒内透明度 1→0（叠化到下一片段）
 * - hard_cut / 无转场：不调制。
 */
export function applyTransitionAlpha(opacity: number, clip: Clip, localT: number): number {
  const dur = Math.max(0.05, clip.transition_duration_sec ?? 0.5);
  const fadeKinds = new Set(['fade', 'dissolve', 'pixel_dissolve', 'slide', 'wipe', 'glitch']);
  if (clip.transition_in && clip.transition_in !== 'hard_cut' && fadeKinds.has(clip.transition_in)) {
    const windowFrac = clamp(localT / (dur / clip.duration_sec), 0, 1);
    opacity *= windowFrac; // 0→1 淡入
  }
  if (clip.transition_out && clip.transition_out !== 'hard_cut' && fadeKinds.has(clip.transition_out)) {
    const windowFrac = clamp((1 - localT) / (dur / clip.duration_sec), 0, 1);
    opacity *= windowFrac; // 1→0 淡出
  }
  return clamp(opacity, 0, 1);
}

/**
 * M4: 蒙版裁剪 — 把当前画布裁剪路径限定到蒙版形状内（矩形或椭圆，基于归一化 rect）。
 * 必须在 clip 绘制前调用（ctx.save 之后），restore 由调用方统一处理。
 */
export function applyMaskClip(
  ctx: CanvasRenderingContext2D,
  clip: Clip,
  fx: number, fy: number, fw: number, fh: number,
) {
  if (!clip.mask_type || clip.mask_type === 'none') return;
  const r = clip.mask_rect ?? { x: 0, y: 0, w: 1, h: 1 };
  const x = clamp(r.x, 0, 1);
  const y = clamp(r.y, 0, 1);
  // 宽高钳制到剩余画面，避免蒙版越界
  const w = clamp(r.w, 0.01, 1 - x);
  const h = clamp(r.h, 0.01, 1 - y);
  const mx = fx + x * fw;
  const my = fy + y * fh;
  const mw = w * fw;
  const mh = h * fh;
  ctx.beginPath();
  if (clip.mask_type === 'ellipse') {
    ctx.ellipse(mx + mw / 2, my + mh / 2, mw / 2, mh / 2, 0, 0, Math.PI * 2);
  } else {
    ctx.rect(mx, my, mw, mh);
  }
  ctx.clip();
}

/**
 * C3: 嵌套序列合成 — 把子时间线的片段按父片段的时间窗口递归渲染。
 * 父片段占 [start_sec, start_sec+duration)；嵌套时间线内的时刻 = t - start_sec。
 * 深度上限 4 防止循环嵌套导致栈溢出。
 */
function drawNestedTimeline(
  ctx: CanvasRenderingContext2D,
  parent: Clip,
  parentTrack: Track,
  fx: number, fy: number, fw: number, fh: number,
  t: number,
  depth: number,
) {
  if (depth >= 4 || !parent.nested_timeline) return;
  const nestedT = t - parent.start_sec;
  const nt = parent.nested_timeline;
  const sorted = orderTracksForComposite(nt.tracks);
  for (const track of sorted) {
    if (track.hidden) continue;
    if (track.muted && (track.kind === 'audio' || track.kind === 'waveform')) continue;
    for (const clip of track.clips) {
      if (nestedT < clip.start_sec || nestedT >= clip.start_sec + clip.duration_sec) continue;
      if (clip.enabled === false) continue;
      if (clip.nested_timeline) {
        // 深度优先：用 clip 自己的时间窗继续下钻
        drawNestedTimeline(ctx, clip, track, fx, fy, fw, fh, clip.start_sec + nestedT, depth + 1);
        continue;
      }
      drawClipToPreview(ctx, clip, track, fx, fy, fw, fh, nestedT);
    }
  }
}

function drawClipToPreview(
  ctx: CanvasRenderingContext2D,
  clip: Clip,
  track: Track,
  fx: number, fy: number, fw: number, fh: number,
  t: number,
) {
  const localT = (t - clip.start_sec) / clip.duration_sec; // 0-1
  const color = TRACK_COLORS[track.kind] ?? '#4F8CFF';

  // Apply keyframe interpolation for opacity/transform if present
  let opacity = clip.opacity;
  const tf: Transform2D = { ...getClipTransform(clip) };
  let speed = clip.speed;
  if (clip.keyframes.length > 0) {
    const props = interpolateProperties(clip.keyframes, localT);
    opacity = props.opacity ?? opacity;
    tf.scale = props.scale ?? tf.scale;
    tf.x = props.position_x ?? tf.x;
    tf.y = props.position_y ?? tf.y;
    tf.rotation = props.rotation ?? tf.rotation;
    // M5: 时间重映射 — 关键帧驱动的变速（预览层）
    if (props.speed !== undefined) speed = props.speed;
  }

  // M11: 转场可见性 — 在进/出转场窗口内对透明度做渐变（淡入淡出/溶解类预览）
  opacity = applyTransitionAlpha(opacity, clip, localT);

  ctx.save();
  ctx.globalAlpha = clamp(opacity, 0, 1);
  if (clip.blend_mode && clip.blend_mode !== 'normal') {
    (ctx as CanvasRenderingContext2D).globalCompositeOperation = clip.blend_mode as GlobalCompositeOperation;
  }

  // M4: 蒙版 — 裁剪到矩形/椭圆内
  applyMaskClip(ctx, clip, fx, fy, fw, fh);

  const fxStr = buildFilter(clip);
  if (fxStr) {
    ctx.filter = fxStr;
  }

  switch (track.kind) {
    case 'video':
    case 'image': {
      // Try real media first (uploaded video/image)
      const entry = mediaManager.get(clip.asset_id);
      const videoEl = entry?.videoEl;

      // Media failed to load (404 / network) — show an explicit placeholder instead of silent black
      if (entry?.error) {
        drawErrorPlaceholder(ctx, fx, fy, fw, fh);
        break;
      }

      let drewReal = false;

      if (track.kind === 'image' && entry?.img) {
        // Real image: draw via the registered image (mediaManager caches + notifies on load)
        const img = entry.img;
        if (img.complete && img.naturalWidth > 0) {
          drawCover(ctx, img, fx, fy, fw, fh, tf);
          drewReal = true;
        }
      } else if (videoEl && videoEl.readyState >= 2) {
        // Real video frame: seek to clip-local time and draw
        const sourceT = (t - clip.start_sec) * speed + clip.source_offset_sec;
        mediaManager.seekVideo(clip.asset_id, sourceT);
        drawCover(ctx, videoEl, fx, fy, fw, fh, tf);
        drewReal = true;
      }

      if (!drewReal) {
        // Placeholder gradient block representing media
        const g = ctx.createLinearGradient(fx, fy, fx + fw, fy + fh);
        g.addColorStop(0, shadeColor(color, -0.5));
        g.addColorStop(0.5, shadeColor(color, -0.2));
        g.addColorStop(1, shadeColor(color, -0.6));
        ctx.fillStyle = g;
        ctx.fillRect(fx, fy, fw, fh);
        // Moving sheen to suggest motion
        const sheenX = fx + ((localT * 2) % 1.4 - 0.2) * fw;
        const sg = ctx.createLinearGradient(sheenX - 80, 0, sheenX + 80, 0);
        sg.addColorStop(0, 'rgba(255,255,255,0)');
        sg.addColorStop(0.5, 'rgba(255,255,255,0.08)');
        sg.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = sg;
        ctx.fillRect(fx, fy, fw, fh);
        // Clip label
        ctx.fillStyle = 'rgba(255,255,255,0.7)';
        ctx.font = "500 14px 'Inter','Noto Sans SC',sans-serif";
        ctx.textBaseline = 'top';
        const label = (clip.metadata?.title as string) || clip.asset_id || track.kind;
        ctx.fillText(label, fx + 16, fy + 14);
      }
      break;
    }
    case 'text':
    case 'caption': {
      const text = clip.text || '文字';
      // Min-dimension scaling keeps caption text fitting portrait frames too
      // (height-only scaling overflows 1080x1920 exports).
      const fontSize = captionFontSize(clip.font_size ?? 48, fw, fh, tf.scale);
      // Font family comes from clip.font (was hardcoded); apply weight + italic
      const fontStyle = clip.font_italic ? 'italic' : 'normal';
      const fontWeight = clip.font_weight || 'normal';
      ctx.font = `${fontStyle} ${fontWeight} ${fontSize}px ${quoteFontFamily(clip.font)}`;
      // Letter spacing (feature-detected; unsupported browsers ignore it)
      const hasLetterSpacing = 'letterSpacing' in ctx;
      const letterSpacingPx = clip.letter_spacing ?? 0;
      if (hasLetterSpacing && letterSpacingPx !== 0) {
        (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${letterSpacingPx}px`;
      }
      // Horizontal alignment from clip.text_align
      const align = clip.text_align ?? 'center';
      ctx.textAlign = align as CanvasTextAlign;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = clip.font_color || '#FFFFFF';
      const baseY = track.kind === 'caption' ? fy + captionBaselineY(fh) : fy + fh / 2;
      const baseX = align === 'left' ? fx + fw * 0.05 : align === 'right' ? fx + fw * 0.95 : fx + fw / 2;
      const maxWidth = fw * 0.9;
      // Apply position offset + rotation about the text anchor
      ctx.save();
      ctx.translate(baseX + tf.x * fw, baseY + tf.y * fh);
      if (tf.rotation !== 0) ctx.rotate((tf.rotation * Math.PI) / 180);

      // Drop shadow (from style fields)
      const shadowColor = clip.shadow_color || '';
      const shadowX = clip.shadow_x ?? 0;
      const shadowY = clip.shadow_y ?? 0;
      const shadowBlur = clip.shadow_blur ?? 0;
      if (shadowBlur > 0 && shadowColor) {
        ctx.shadowColor = shadowColor;
        ctx.shadowOffsetX = shadowX;
        ctx.shadowOffsetY = shadowY;
        ctx.shadowBlur = shadowBlur;
      }

      // Glow pass — blurred halo rendered behind the glyphs, offsets zeroed
      const glowColor = clip.glow_color || '';
      const glowWidth = clip.glow_width ?? 0;
      if (glowWidth > 0 && glowColor) {
        ctx.save();
        ctx.shadowColor = glowColor;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
        ctx.shadowBlur = glowWidth;
        ctx.fillText(text, 0, 0, maxWidth);
        ctx.restore();
      }

      // Stroke before fill so the fill covers the inner half of the outline
      const strokeColor = clip.stroke_color || '#000000';
      const strokeWidth = clip.stroke_width ?? 0;
      if (strokeWidth > 0 && strokeColor) {
        ctx.lineWidth = strokeWidth;
        ctx.lineJoin = 'round';
        ctx.strokeStyle = strokeColor;
        ctx.strokeText(text, 0, 0, maxWidth);
      }
      ctx.fillText(text, 0, 0, maxWidth);
      ctx.restore();

      if (hasLetterSpacing && letterSpacingPx !== 0) {
        (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = '0px';
      }
      ctx.textAlign = 'left';
      break;
    }
    case 'animation': {
      // 关键帧插值已在 switch 之前统一执行（opacity/tf 已含插值结果），此处不重复调用
      // interpolateProperties，只把 translate/rotate/opacity 应用到形状渲染上。
      // 无关键帧时保留正弦缩放作为退化行为。
      const cx = fx + fw / 2 + tf.x * fw;
      const cy = fy + fh / 2 + tf.y * fh;
      const pulse = clip.keyframes.length > 0 ? 1 : 0.8 + 0.2 * Math.sin(localT * Math.PI * 2);
      const size = (fh * 0.15) * tf.scale * pulse;
      const shape = (typeof clip.metadata?.shape === 'string' && clip.metadata.shape) || clip.shape || 'rect';
      ctx.save();
      ctx.globalAlpha = clamp(opacity, 0, 1) * 0.85;
      ctx.translate(cx, cy);
      if (tf.rotation !== 0) ctx.rotate((tf.rotation * Math.PI) / 180);
      ctx.fillStyle = color;
      ctx.beginPath();
      if (shape === 'ellipse' || shape === 'circle') {
        ctx.arc(0, 0, size, 0, Math.PI * 2);
      } else {
        // 默认圆角矩形
        const rr = Math.min(size, fw * 0.05);
        ctx.moveTo(-size + rr, -size);
        ctx.arcTo(size, -size, size, size, rr);
        ctx.arcTo(size, size, -size, size, rr);
        ctx.arcTo(-size, size, -size, -size, rr);
        ctx.arcTo(-size, -size, size, -size, rr);
        ctx.closePath();
      }
      ctx.fill();
      ctx.restore();
      break;
    }
    case 'shape': {
      ctx.fillStyle = clip.fill || color;
      ctx.fillRect(fx + fw * 0.3, fy + fh * 0.3, fw * 0.4, fh * 0.4);
      break;
    }
    default:
      break; // audio etc. not drawn to video frame
  }

  ctx.restore();
}

export interface Transform2D {
  /** Position offset from frame center, normalized (-1..1 of frame size) */
  x: number;
  y: number;
  scale: number;
  /** Rotation in degrees */
  rotation: number;
}

export const IDENTITY_TRANSFORM: Transform2D = { x: 0, y: 0, scale: 1, rotation: 0 };

/**
 * C1: 画布双击命中测试 — 返回 playhead 处、命中框内最顶层的 text/caption 片段。
 * 命中框以文本锚点为中心：左右 fw*0.45、上下 fh*0.3。
 */
export function hitTestTextClipForEdit(
  px: number, py: number,
  frame: FrameRect,
  tracks: Track[],
  t: number,
): { clip: Clip; x: number; y: number } | null {
  const sorted = orderTracksForComposite(tracks).slice().reverse();
  for (const track of sorted) {
    if (track.hidden) continue;
    if (track.kind !== 'text' && track.kind !== 'caption') continue;
    const clip = track.clips.find((c) => t >= c.start_sec && t < c.start_sec + c.duration_sec && c.enabled !== false);
    if (!clip) continue;
    const align = clip.text_align ?? 'center';
    const baseY = track.kind === 'caption' ? frame.fy + captionBaselineY(frame.fh) : frame.fy + frame.fh / 2;
    const tf = getClipTransform(clip);
    const ax = (align === 'left' ? frame.fx + frame.fw * 0.05 : align === 'right' ? frame.fx + frame.fw * 0.95 : frame.fx + frame.fw / 2) + tf.x * frame.fw;
    const ay = baseY + tf.y * frame.fh;
    if (Math.abs(px - ax) < frame.fw * 0.45 && Math.abs(py - ay) < frame.fh * 0.3) {
      return { clip, x: ax, y: ay };
    }
  }
  return null;
}

/** Read a clip's base transform from metadata (static edit) — keyframes animate on top. */
export function getClipTransform(clip: Clip): Transform2D {
  const t = (clip.metadata?.transform ?? {}) as Partial<Transform2D>;
  return {
    x: t.x ?? 0,
    y: t.y ?? 0,
    scale: t.scale ?? 1,
    rotation: t.rotation ?? 0,
  };
}

/**
 * Build a canvas-safe font-family token from a single family name.
 * Multi-word families (e.g. "Noto Sans SC") must be quoted; null → default stack.
 */
function quoteFontFamily(family: string | null | undefined): string {
  if (!family) return "'Noto Sans SC','PingFang SC',sans-serif";
  if (/^['"]/.test(family)) return family; // already quoted / a full stack
  return family.includes(' ') ? `'${family}'` : family;
}

/**
 * Draw a "素材加载失败" placeholder for media that failed to load (entry.error).
 * Dark box + centered label — deliberately minimal (fillRect + fillText).
 */
function drawErrorPlaceholder(
  ctx: CanvasRenderingContext2D,
  fx: number, fy: number, fw: number, fh: number,
) {
  ctx.fillStyle = '#16181F';
  ctx.fillRect(fx, fy, fw, fh);
  ctx.fillStyle = 'rgba(255,255,255,0.6)';
  ctx.font = "500 14px 'Inter','Noto Sans SC',sans-serif";
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('素材加载失败', fx + fw / 2, fy + fh / 2);
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
}

/**
 * Draw an image/video source covering the frame rect (object-fit: cover),
 * applying position offset, scale and rotation about the frame center.
 */
function drawCover(
  ctx: CanvasRenderingContext2D,
  src: HTMLVideoElement | HTMLImageElement,
  fx: number, fy: number, fw: number, fh: number,
  tf: Transform2D,
) {
  const sw = (src as HTMLVideoElement).videoWidth || (src as HTMLImageElement).naturalWidth;
  const sh = (src as HTMLVideoElement).videoHeight || (src as HTMLImageElement).naturalHeight;
  if (!sw || !sh) return;
  // cover fit
  const srcAspect = sw / sh;
  const dstAspect = fw / fh;
  let cw = sw, ch = sh, cx = 0, cy = 0;
  if (srcAspect > dstAspect) {
    cw = sh * dstAspect;
    cx = (sw - cw) / 2;
  } else {
    ch = sw / dstAspect;
    cy = (sh - ch) / 2;
  }

  // Apply transform about the frame center
  const centerX = fx + fw / 2;
  const centerY = fy + fh / 2;
  const scale = tf.scale;
  const dw = fw * scale, dh = fh * scale;

  ctx.save();
  // Clip to the frame rect so scaled/offset/rotated media doesn't bleed into the letterbox
  ctx.beginPath();
  ctx.rect(fx, fy, fw, fh);
  ctx.clip();
  ctx.translate(centerX + tf.x * fw, centerY + tf.y * fh);
  if (tf.rotation !== 0) ctx.rotate((tf.rotation * Math.PI) / 180);
  try {
    ctx.drawImage(src, cx, cy, cw, ch, -dw / 2, -dh / 2, dw, dh);
  } catch { /* frame not ready */ }
  ctx.restore();
}

function buildFilter(clip: Clip): string {
  const parts: string[] = [];
  if (clip.fx_brightness != null && clip.fx_brightness !== 1) parts.push(`brightness(${clip.fx_brightness})`);
  if (clip.fx_contrast != null && clip.fx_contrast !== 1) parts.push(`contrast(${clip.fx_contrast})`);
  if (clip.fx_saturation != null && clip.fx_saturation !== 1) parts.push(`saturate(${clip.fx_saturation})`);
  if (clip.fx_blur != null && clip.fx_blur > 0) parts.push(`blur(${clip.fx_blur}px)`);
  if (clip.fx_hue != null && clip.fx_hue !== 0) parts.push(`hue-rotate(${clip.fx_hue}deg)`);
  return parts.join(' ');
}

function shadeColor(hex: string, amt: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const f = (v: number) => clamp(Math.round(amt > 0 ? v + (255 - v) * amt : v * (1 + amt)), 0, 255);
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

import { useEffect as useEffect2, useRef as useRef2 } from 'react';

function AudioLevelMeter() {
  const canvasRef = useRef2<HTMLCanvasElement>(null);
  const rafRef = useRef2(0);

  useEffect2(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const tick = () => {
      const [left, right] = mediaManager.getAudioLevels();
      const w = canvas.width;
      const h = canvas.height;

      ctx.clearRect(0, 0, w, h);

      // Background
      ctx.fillStyle = '#1a1a24';
      ctx.fillRect(0, 0, w, h);

      // Left channel (upper half)
      const lw = Math.round(left * (w - 2));
      ctx.fillStyle = left > 0.9 ? '#ef4444' : left > 0.6 ? '#f59e0b' : '#34D399';
      ctx.fillRect(1, 1, lw, h / 2 - 2);

      // Right channel (lower half)
      const rw = Math.round(right * (w - 2));
      ctx.fillStyle = right > 0.9 ? '#ef4444' : right > 0.6 ? '#f59e0b' : '#34D399';
      ctx.fillRect(1, h / 2 + 1, rw, h / 2 - 2);

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      width={80}
      height={16}
      className="rounded-cw-xs border border-outline-variant/20"
      style={{ imageRendering: 'pixelated' }}
    />
  );
}

/**
 * 播放速度循环：从 speeds 列表中选择下一个档位。
 * 若当前值不在列表中（例如来自其他路径的自定义速度），从头档开始。
 * Advance the play-speed cycle: pick the next entry in `speeds`.
 * If `current` is not present (a custom value from another path), start from speeds[0].
 * Empty list → return `current` unchanged (graceful no-op).
 */
export function nextPlaybackSpeed(current: number, speeds: number[]): number {
  if (speeds.length === 0) return current;
  const idx = speeds.indexOf(current);
  const nextIndex = idx === -1 ? 0 : (idx + 1) % speeds.length;
  return speeds[nextIndex];
}
