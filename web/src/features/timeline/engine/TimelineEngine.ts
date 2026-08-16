/**
 * TimelineEngine — Canvas 2D multi-track timeline engine.
 * Manages viewport (zoom/scroll), the render loop, and all pointer interactions
 * (scrub, select, marquee, move, trim, snap, pan).
 */
import type { Track, Clip } from '@/types/timeline';
import { useTimelineStore } from '@/stores/timelineStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { usePreviewStore } from '@/stores/previewStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { normalizeClipKind } from '@/lib/utils';
import { useHistoryStore } from '@/stores/historyStore';
import {
  makeLayout, xToTime, yToTrackIndex, timeToX, trackToY,
  makeDragState, scrollbarGeom, MIN_ZOOM, MAX_ZOOM, DEFAULT_ZOOM, TRIM_HANDLE_PX,
  type TimelineLayout, type DragState, type Marker,
} from './types';
import {
  drawBackground, drawTrackLanes, drawRuler, drawTrackHeaders,
  drawClip, drawPlayhead, drawMarkers, drawSnapGuide, drawMarquee, drawEmptyState,
  drawHorizontalScrollbar,
} from './renderers';
import { collectSnapTargets, applySnap } from './snap';
import { clamp } from '@/lib/utils';
import { mediaManager } from '@/services/media/mediaManager';

export class TimelineEngine {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private dpr = 1;
  private cssW = 0;
  private cssH = 0;

  // Viewport
  zoom = DEFAULT_ZOOM;
  scrollX = 0;
  scrollY = 0;

  // Interaction
  private drag: DragState = makeDragState();
  private hoveredClipId: string | null = null;
  private hoveredTrackId: string | null = null;
  private scrollbarHover = false;
  private lastClickTime = 0;
  private lastClickX = 0;
  markers: Marker[] = [];
  /** M14: 范围工具点击回调（面板接线：两击设置 In/Out 区间） */
  onRangePoint: ((t: number) => void) | null = null;
  /** M8: 标记变更回调（增/删/改名后触发；面板接线写回 timelineStore 持久化） */
  onMarkersChange: ((markers: Marker[]) => void) | null = null;
  /** M8: 双击已有标记触发重命名（面板接线弹出命名输入框） */
  onMarkerRename: ((time: number) => void) | null = null;
  /** Drop feedback animation: green=placed before/after, red=reject (middle) */
  dropFeedback: { type: 'before' | 'after' | 'reject'; clipId: string; trackId: string; time: number } | null = null;
  private feedbackTimer: ReturnType<typeof setTimeout> | null = null;

  private rafId = 0;
  private dirty = true;
  private disposed = false;
  private unsubscribers: (() => void)[] = [];
  private resizeObserver: ResizeObserver | null = null;
  /** C2: Alt 键当前是否按住（用于悬停光标切换） */
  private altKeyHeld = false;
  private _keyHandler: ((e: KeyboardEvent) => void) | null = null;
  private _keyUnsubs: (() => void)[] | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('TimelineEngine: Canvas 2D context unavailable');
    }
    this.ctx = ctx;

    this.resize();
    this.bindStoreSubscriptions();
    this.bindPointerEvents();
    this.bindWheelEvent();
    this.bindResizeObserver();
    this.loop();
  }

  // ── lifecycle ────────────────────────────────────────
  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.rafId);
    this.unsubscribers.forEach((u) => u());
    this.removePointerEvents();
    this.canvas.removeEventListener('wheel', this.onWheel);
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
  }

  requestRender() {
    this.dirty = true;
  }

  /** Show drop feedback animation (green flash = placed, red shake = rejected) */
  private showDropFeedback(type: 'before' | 'after' | 'reject', clipId: string, trackId: string, time: number) {
    if (this.feedbackTimer) clearTimeout(this.feedbackTimer);
    this.dropFeedback = { type, clipId, trackId, time };
    this.dirty = true;
    this.feedbackTimer = setTimeout(() => {
      this.dropFeedback = null;
      this.dirty = true;
    }, 600);
  }

  resize() {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.cssW = Math.max(1, rect.width);
    this.cssH = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.cssW * this.dpr);
    this.canvas.height = Math.round(this.cssH * this.dpr);
    this.canvas.style.width = `${this.cssW}px`;
    this.canvas.style.height = `${this.cssH}px`;
    this.clampScroll();
    this.dirty = true;
  }

  private bindResizeObserver() {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    this.resizeObserver = new ResizeObserver(() => {
      if (this.disposed) return;
      this.resize();
    });
    this.resizeObserver.observe(parent);
  }

  // ── store subscriptions ──────────────────────────────
  private bindStoreSubscriptions() {
    const mark = () => this.requestRender();
    this.unsubscribers.push(useTimelineStore.subscribe(mark));
    this.unsubscribers.push(useSelectionStore.subscribe(mark));
    this.unsubscribers.push(usePreviewStore.subscribe(mark));
    this.unsubscribers.push(useSettingsStore.subscribe(mark));
    this.unsubscribers.push(mediaManager.onChange(mark));
  }

  private layout(): TimelineLayout {
    return makeLayout(this.cssW, this.cssH, this.zoom, this.scrollX, this.scrollY);
  }

  // ── render loop ──────────────────────────────────────
  private loop = () => {
    if (this.disposed) return;
    // Always clamp scroll — keeps viewport valid after timeline changes (e.g., all clips deleted)
    this.clampScroll();
    // Keep animating while drop feedback is active
    if (this.dropFeedback) this.dirty = true;
    if (this.dirty) {
      this.dirty = false;
      try {
        this.render();
      } catch (err) {
        // A single bad frame must never kill the render loop permanently —
        // log it and keep the loop alive so the next frame can recover.
        console.error('[TimelineEngine] render error:', err);
      }
    }
    this.rafId = requestAnimationFrame(this.loop);
  };

  private render() {
    const { ctx, dpr } = this;
    const L = this.layout();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const timeline = useTimelineStore.getState().timeline;
    const selection = useSelectionStore.getState();
    const playhead = usePreviewStore.getState().currentTimeSec;
    const tracks = timeline.tracks;

    drawBackground(ctx, L);
    drawTrackLanes(ctx, L, tracks, selection.selectedTrackId);

    // Clips (with drag ghosts)
    if (tracks.length === 0) {
      drawEmptyState(ctx, L);
    } else {
      const isMoving = this.drag.mode === 'move-clip';
      for (let i = 0; i < tracks.length; i++) {
        if (tracks[i].hidden) continue; // M7: 隐藏轨道不渲染
        for (const clip of tracks[i].clips) {
          const isDragged = isMoving && this.drag.origClips.has(clip.id);
          if (isDragged) continue; // draw ghosts after, on top
          drawClip(ctx, L, tracks[i], i, clip, {
            selected: selection.selectedClipIds.includes(clip.id),
            hovered: clip.id === this.hoveredClipId,
          });
        }
      }
      // Draw dragged ghosts on top
      if (isMoving) {
        for (const [id, orig] of this.drag.origClips) {
          const track = tracks.find((t) => t.clips.some((c) => c.id === id))
            ?? tracks.find((t) => t.id === orig.track_id);
          if (!track) continue;
          const tIdx = tracks.indexOf(track);
          drawClip(ctx, L, track, tIdx, orig, {
            selected: true,
            hovered: false,
            isDragGhost: true,
            ghostDeltaTime: this.drag.deltaTime,
            ghostDeltaTrack: this.drag.deltaTrack,
          });
        }
      }
      // Trim ghost
      if ((this.drag.mode === 'trim-start' || this.drag.mode === 'trim-end') && this.drag.trimOrig) {
        const orig = this.drag.trimOrig;
        const track = tracks.find((t) => t.id === orig.track_id);
        if (track) {
          const tIdx = tracks.indexOf(track);
          const ghost = this.computeTrimGhost();
          if (ghost) {
            drawClip(ctx, L, track, tIdx, ghost, {
              selected: true, hovered: false, isDragGhost: true,
            });
          }
        }
      }
    }

    drawMarkers(ctx, L, this.markers);
    if (this.drag.snapX !== null) drawSnapGuide(ctx, L, this.drag.snapX);
    if (this.drag.marquee) drawMarquee(ctx, this.drag.marquee);
    drawTrackHeaders(ctx, L, tracks, selection.selectedTrackId, this.hoveredTrackId);
    drawRuler(ctx, L, timeline.fps);
    drawPlayhead(ctx, L, playhead);

    // Drop feedback animation
    if (this.dropFeedback) {
      const fb = this.dropFeedback;
      const track = tracks.find((t) => t.id === fb.trackId);
      const clip = track?.clips.find((c) => c.id === fb.clipId);
      if (track && clip) {
        const tIdx = tracks.indexOf(track);
        const x = timeToX(clip.start_sec, L);
        const w = clip.duration_sec * L.zoom;
        const y = trackToY(tIdx, L);
        const h = L.trackH - 6;
        const elapsed = Date.now() % 600;
        if (fb.type === 'reject') {
          // Red shake — oscillate horizontally
          const shake = Math.sin(elapsed / 40) * 4 * (1 - elapsed / 600);
          ctx.save();
          ctx.strokeStyle = 'rgba(244,67,54,0.9)';
          ctx.lineWidth = 2;
          ctx.setLineDash([4, 3]);
          ctx.strokeRect(x + shake + 1, y + 4, w - 2, h - 2);
          ctx.restore();
        } else {
          // Green flash — pulse the placed clip outline
          const alpha = 0.9 * (1 - elapsed / 600);
          ctx.save();
          ctx.strokeStyle = `rgba(52,211,153,${alpha})`;
          ctx.lineWidth = 2.5;
          ctx.strokeRect(x + 1, y + 4, w - 2, h - 2);
          ctx.restore();
        }
      }
    }

    // Horizontal scrollbar — drawn last so it sits on top
    const sbState = this.drag.mode === 'scrollbar' ? 'drag' : this.scrollbarHover ? 'hover' : 'idle';
    drawHorizontalScrollbar(ctx, L, timeline.duration_sec * L.zoom, sbState);
  }

  // ── pointer interactions ─────────────────────────────
  private onPointerDown = (e: PointerEvent) => {
    this.canvas.setPointerCapture(e.pointerId);
    const L = this.layout();
    const { x, y } = this.localPos(e);
    const timeline = useTimelineStore.getState().timeline;
    const selection = useSelectionStore.getState();
    const preview = usePreviewStore.getState();

    this.drag = makeDragState();
    this.drag.startMouse = { x, y };
    this.drag.startTime = xToTime(x, L);

    // Middle button → pan
    if (e.button === 1) {
      this.drag.mode = 'pan';
      return;
    }

    // Ruler → scrub or double-click to add marker (M8: dblclick on existing marker → rename)
    if (y < L.rulerH) {
      const now = performance.now();
      const clickTime = xToTime(x, L);
      if (now - this.lastClickTime < 400 && Math.abs(x - this.lastClickX) < 10) {
        // 命中已有标记（6px 内）→ 触发重命名；否则新增
        const near = this.markers.find((m) => Math.abs(timeToX(m.time, L) - x) < 6);
        if (near) {
          this.onMarkerRename?.(near.time);
        } else {
          this.markers.push({ time: Math.max(0, clickTime) });
          this.markers.sort((a, b) => a.time - b.time);
          this.requestRender();
          this.onMarkersChange?.(this.markers);
        }
        this.drag.mode = 'none';
        return;
      }
      this.lastClickTime = now;
      this.lastClickX = x;
      this.drag.mode = 'scrub';
      // Pause playback so the playhead doesn't fight the user's scrub drag
      preview.setPlaying(false);
      preview.setCurrentTime(Math.max(0, clickTime));
      return;
    }

    // Header → select track
    if (x < L.headerW) {
      const tIdx = yToTrackIndex(y, L);
      if (tIdx >= 0 && tIdx < timeline.tracks.length) {
        selection.selectTrack(timeline.tracks[tIdx].id);
      }
      return;
    }

    // Horizontal scrollbar → drag to scroll
    if (y >= L.height - L.scrollbarH && x >= L.headerW) {
      const g = this.hScrollbar();
      if (g.maxX > 0) {
        this.drag.mode = 'scrollbar';
        if (x >= g.thumbX && x <= g.thumbX + g.thumbW) {
          // Grab the thumb directly
          this.drag.scrollbarGrabOffset = x - g.thumbX;
        } else {
          // Click on the track → jump so the thumb centres on the click, then drag
          this.drag.scrollbarGrabOffset = g.thumbW / 2;
          this.applyScrollbarScroll(x);
        }
      }
      return;
    }

    const hit = this.hitTestClip(x, y);

    // Razor tool → split
    if (selection.toolMode === 'razor' && hit) {
      const t = xToTime(x, L);
      useHistoryStore.getState().pushState(timeline, 'split');
      useTimelineStore.getState().splitClip(hit.clip.id, t);
      return;
    }

    // M14: 范围工具 → 两击设置区间（回调由面板接线，设置 In/Out loopRegion）
    if (selection.toolMode === 'range') {
      this.onRangePoint?.(xToTime(x, L));
      return;
    }

    if (hit) {
      const { clip, track } = hit;
      // C2: Alt+拖拽音频/波形片段 → 调整增益（音量）
      if (e.altKey && (track.kind === 'audio' || track.kind === 'waveform')) {
        this.altKeyHeld = true;
        this.drag.mode = 'gain';
        this.drag.gainClipId = clip.id;
        this.drag.gainStartVolume = clip.volume;
        if (!this.drag.historyPushed) {
          useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'gain');
          this.drag.historyPushed = true;
        }
        selection.selectClip(clip.id, e.shiftKey || e.ctrlKey || e.metaKey);
        selection.selectTrack(track.id);
        this.requestRender();
        return;
      }
      // Select (Shift=additive, Ctrl/Meta=toggle)
      if (!selection.selectedClipIds.includes(clip.id)) {
        selection.selectClip(clip.id, e.shiftKey || e.ctrlKey || e.metaKey);
      } else if (e.ctrlKey || e.metaKey) {
        // Ctrl+click selected clip → deselect it, don't start drag
        selection.selectClip(clip.id, true);
        selection.selectTrack(track.id);
        return;
      }
      // Shift+click on already-selected clip → keep selection (no-op for select)
      selection.selectTrack(track.id);

      // Determine move vs trim
      const clipX = timeToX(clip.start_sec, L);
      const clipW = clip.duration_sec * L.zoom;
      const selIds = useSelectionStore.getState().selectedClipIds;

      if (selIds.includes(clip.id) && x - clipX < TRIM_HANDLE_PX) {
        this.drag.mode = 'trim-start';
        this.drag.trimClipId = clip.id;
        this.drag.trimOrig = clip;
        useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'trim');
        this.drag.historyPushed = true;
      } else if (selIds.includes(clip.id) && clipX + clipW - x < TRIM_HANDLE_PX) {
        this.drag.mode = 'trim-end';
        this.drag.trimClipId = clip.id;
        this.drag.trimOrig = clip;
        useHistoryStore.getState().pushState(useTimelineStore.getState().timeline, 'trim');
        this.drag.historyPushed = true;
      } else {
        // Begin move for all selected clips (M2: 展开同组片段一起移动)
        this.drag.mode = 'move-clip';
        const tl = useTimelineStore.getState().timeline;
        const store = useTimelineStore.getState();
        const moveIds = new Set(useSelectionStore.getState().selectedClipIds);
        // 组扩散：任一选中片段属于某组 → 整组加入移动集
        for (const id of [...moveIds]) {
          for (const gid of store.getGroupClipIds(id)) moveIds.add(gid);
        }
        for (const id of moveIds) {
          for (const tr of tl.tracks) {
            const c = tr.clips.find((cc) => cc.id === id);
            if (c) this.drag.origClips.set(id, c);
          }
        }
        if (!this.drag.historyPushed) {
          useHistoryStore.getState().pushState(tl, 'move');
          this.drag.historyPushed = true;
        }
      }
    } else {
      // Empty area → marquee
      this.drag.mode = 'marquee';
      this.drag.marquee = { x0: x, y0: y, x1: x, y1: y };
      if (!e.shiftKey) selection.deselectAll();
    }

    this.requestRender();
  };

  private onPointerMove = (e: PointerEvent) => {
    const L = this.layout();
    const { x, y } = this.localPos(e);

    if (this.drag.mode === 'none') {
      this.updateHover(x, y);
      this.updateCursor(x, y);
      return;
    }

    const settings = useSettingsStore.getState();

    switch (this.drag.mode) {
      case 'scrub': {
        usePreviewStore.getState().setCurrentTime(Math.max(0, xToTime(x, L)));
        // 拖动播放头越过画布左右边缘时联动水平滚动（指针已 capture，越界事件仍可达）。
        // 行为边界：滚动仅在 pointermove 事件驱动（指针停在边缘外不动则不继续滚动）。
        const EDGE = 24;
        if (x > L.width - EDGE) {
          this.scrollX += (x - (L.width - EDGE)) * 1.2;
          this.clampScroll();
          this.requestRender();
        } else if (x < L.headerW + EDGE) {
          this.scrollX -= ((L.headerW + EDGE) - x) * 1.2;
          this.clampScroll();
          this.requestRender();
        }
        break;
      }
      case 'pan': {
        const dx = x - this.drag.startMouse.x;
        const dy = y - this.drag.startMouse.y;
        this.scrollX = Math.max(0, this.scrollX - dx);
        this.scrollY = Math.max(0, this.scrollY - dy);
        this.drag.startMouse = { x, y };
        this.clampScroll();
        this.requestRender();
        break;
      }
      case 'scrollbar': {
        this.canvas.style.cursor = 'grabbing';
        this.applyScrollbarScroll(x);
        break;
      }
      case 'marquee': {
        this.drag.marquee = { ...this.drag.marquee!, x1: x, y1: y };
        this.requestRender();
        break;
      }
      case 'move-clip': {
        const rawDelta = xToTime(x, L) - this.drag.startTime;
        const tl = useTimelineStore.getState().timeline;
        const ids = new Set(this.drag.origClips.keys());
        const targets = collectSnapTargets(
          tl.tracks, ids,
          usePreviewStore.getState().currentTimeSec,
          this.markers,
        );
        // Candidate edges = min start and max end of dragged clips
        let minStart = Infinity, maxEnd = -Infinity;
        for (const c of this.drag.origClips.values()) {
          minStart = Math.min(minStart, c.start_sec);
          maxEnd = Math.max(maxEnd, c.start_sec + c.duration_sec);
        }
        const snapped = settings.snapEnabled
          ? applySnap([minStart, maxEnd], rawDelta, targets, L, settings.snapThresholdPx)
          : { deltaTime: rawDelta, snapX: null };

        // Prevent moving before 0
        const minDelta = -minStart;
        this.drag.deltaTime = Math.max(minDelta, snapped.deltaTime);
        this.drag.snapX = snapped.snapX;

        // Vertical track delta (Shift constrains horizontal-only)
        if (!e.shiftKey) {
          const startTrackIdx = this.trackIndexOf(this.drag.origClips.values().next().value?.track_id);
          const curTrackIdx = yToTrackIndex(y, L);
          this.drag.deltaTrack = curTrackIdx - startTrackIdx;
        }

        this.requestRender();
        break;
      }
      case 'trim-start':
      case 'trim-end': {
        this.drag.snapX = null;
        this.requestRender();
        break;
      }
      case 'gain': {
        // C2: 垂直拖拽 = 增益变化；向上放大，向下衰减。灵敏度 ~1/120 音量/px。
        const id = this.drag.gainClipId;
        if (id) {
          const dy = this.drag.startMouse.y - y;
          const vol = clamp(this.drag.gainStartVolume + dy / 120, 0, 2);
          useTimelineStore.getState().updateClip(id, { volume: Math.round(vol * 100) / 100 });
        }
        this.requestRender();
        break;
      }
    }
  };

  private onPointerUp = (e: PointerEvent) => {
    try { this.canvas.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    const L = this.layout();
    const store = useTimelineStore.getState();

    switch (this.drag.mode) {
      case 'move-clip': {
        const dt = this.drag.deltaTime;
        const dtr = this.drag.deltaTrack;
        if (Math.abs(dt) > 0.001 || dtr !== 0) {
          const tl = store.timeline;
          for (const [id, orig] of this.drag.origClips) {
            let targetTrackId = orig.track_id;
            if (dtr !== 0) {
              const origIdx = this.trackIndexOf(orig.track_id);
              const newIdx = clamp(origIdx + dtr, 0, tl.tracks.length - 1);
              const candidate = tl.tracks[newIdx];
              if (candidate && candidate.kind === tl.tracks[origIdx].kind) {
                targetTrackId = candidate.id;
              }
            }
            const newStartSec = Math.max(0, orig.start_sec + dt);
            const targetTrack = tl.tracks.find((t) => t.id === targetTrackId);
            if (!targetTrack) continue;

            const dur = orig.duration_sec;
            // Check whether a candidate start position is free of overlap (excluding self)
            const isFree = (start: number) =>
              start >= 0 && !targetTrack.clips.some(
                (c) => c.id !== id && c.start_sec < start + dur && c.start_sec + c.duration_sec > start,
              );

            // Find the first overlapping clip (excluding self)
            const overlapping = targetTrack.clips.find(
              (c) => c.id !== id && c.start_sec < newStartSec + dur && c.start_sec + c.duration_sec > newStartSec,
            );

            if (!overlapping) {
              // No collision → place directly
              store.moveClip(id, targetTrackId, newStartSec);
              continue;
            }

            // Collision: determine drop zone by position within the overlapping clip
            // 10% front → place before · 80% middle → reject · 10% back → place after
            const clipStart = overlapping.start_sec;
            const clipEnd = overlapping.start_sec + overlapping.duration_sec;
            const clipDur = overlapping.duration_sec;
            const dropCenter = newStartSec + dur / 2;
            const relPos = clipDur > 0 ? (dropCenter - clipStart) / clipDur : 0.5;

            let placeAt: number | null = null;
            let zone: 'before' | 'after' | 'reject' = 'reject';
            if (relPos < 0.1) {
              zone = 'before';
              placeAt = clipStart - dur;
            } else if (relPos > 0.9) {
              zone = 'after';
              placeAt = clipEnd;
            }

            // Final validation: only commit if the target position is actually free
            if (placeAt !== null && isFree(placeAt)) {
              store.moveClip(id, targetTrackId, placeAt);
              this.showDropFeedback(zone, id, targetTrackId, placeAt);
            } else {
              // No valid placement (middle zone, or before/after would still overlap) → reject
              this.showDropFeedback('reject', id, targetTrackId, newStartSec);
            }
          }
        }
        break;
      }
      case 'trim-start': {
        const ghost = this.computeTrimGhost();
        if (ghost && this.drag.trimOrig) {
          // M1: Alt+trim-start → rolling 编辑（边界共享，此消彼长）
          if (this.altKeyHeld) {
            store.rollingTrim(this.drag.trimOrig.id, ghost.start_sec - this.drag.trimOrig.start_sec, 'start');
          } else {
            store.updateClip(this.drag.trimOrig.id, {
              start_sec: ghost.start_sec,
              duration_sec: ghost.duration_sec,
              source_offset_sec: ghost.source_offset_sec,
            });
          }
        }
        break;
      }
      case 'trim-end': {
        const ghost = this.computeTrimGhost();
        if (ghost && this.drag.trimOrig) {
          // M1: Alt+trim-end → rolling 编辑
          if (this.altKeyHeld) {
            store.rollingTrim(this.drag.trimOrig.id, ghost.duration_sec - this.drag.trimOrig.duration_sec, 'end');
          } else {
            store.updateClip(this.drag.trimOrig.id, { duration_sec: ghost.duration_sec });
          }
        }
        break;
      }
      case 'marquee': {
        if (this.drag.marquee) {
          const m = this.drag.marquee;
          const t0 = xToTime(Math.min(m.x0, m.x1), L);
          const t1 = xToTime(Math.max(m.x0, m.x1), L);
          const tr0 = yToTrackIndex(Math.min(m.y0, m.y1), L);
          const tr1 = yToTrackIndex(Math.max(m.y0, m.y1), L);
          const tl = store.timeline;
          const trackIds = tl.tracks
            .filter((_, i) => i >= tr0 && i <= tr1)
            .map((t) => t.id);
          useSelectionStore.getState().selectClipsInRange(t0, t1, trackIds);
        }
        break;
      }
    }

    this.drag = makeDragState();
    this.requestRender();
  };

  private onPointerCancel = () => {
    // 系统手势/中断：放弃当前拖拽，不提交任何变更，避免 drag 状态卡死
    this.drag = makeDragState();
    this.requestRender();
  };

  private computeTrimGhost(): Clip | null {
    const orig = this.drag.trimOrig;
    if (!orig) return null;
    const L = this.layout();
    const { x } = this.lastMouse;
    const t = xToTime(x, L);

    if (this.drag.mode === 'trim-start') {
      const end = orig.start_sec + orig.duration_sec;
      const newStart = clamp(t, 0, end - 0.1);
      const delta = newStart - orig.start_sec;
      return {
        ...orig,
        start_sec: newStart,
        duration_sec: end - newStart,
        source_offset_sec: Math.max(0, orig.source_offset_sec + delta * orig.speed),
      };
    }
    if (this.drag.mode === 'trim-end') {
      const newEnd = Math.max(orig.start_sec + 0.1, t);
      return { ...orig, duration_sec: newEnd - orig.start_sec };
    }
    return null;
  }

  private lastMouse = { x: 0, y: 0 };

  private hitTestClip(x: number, y: number): { clip: Clip; track: Track } | null {
    const L = this.layout();
    const tl = useTimelineStore.getState().timeline;
    const tIdx = yToTrackIndex(y, L);
    if (tIdx < 0 || tIdx >= tl.tracks.length) return null;
    const track = tl.tracks[tIdx];
    if (track.locked) return null;
    const t = xToTime(x, L);
    for (const clip of track.clips) {
      if (t >= clip.start_sec && t <= clip.start_sec + clip.duration_sec) {
        return { clip, track };
      }
    }
    return null;
  }

  private trackIndexOf(trackId?: string): number {
    if (!trackId) return 0;
    const idx = useTimelineStore.getState().timeline.tracks.findIndex((t) => t.id === trackId);
    return idx === -1 ? 0 : idx;
  }

  private updateHover(x: number, y: number) {
    const L = this.layout();
    // Don't highlight clips/tracks while the pointer is over the scrollbar
    if (y >= L.height - L.scrollbarH && x >= L.headerW) {
      if (this.hoveredClipId !== null || this.hoveredTrackId !== null) {
        this.hoveredClipId = null;
        this.hoveredTrackId = null;
        this.requestRender();
      }
      return;
    }
    const hit = this.hitTestClip(x, y);
    const newHover = hit?.clip.id ?? null;
    const tIdx = yToTrackIndex(y, L);
    const tl = useTimelineStore.getState().timeline;
    const newTrackHover = tIdx >= 0 && tIdx < tl.tracks.length ? tl.tracks[tIdx].id : null;

    if (newHover !== this.hoveredClipId || newTrackHover !== this.hoveredTrackId) {
      this.hoveredClipId = newHover;
      this.hoveredTrackId = newTrackHover;
      this.requestRender();
    }
  }

  private updateCursor(x: number, y: number) {
    const L = this.layout();
    // Scrollbar hover → brighten thumb + grab cursor
    const inScrollbar = y >= L.height - L.scrollbarH && x >= L.headerW && this.hScrollbar().maxX > 0;
    if (inScrollbar !== this.scrollbarHover) {
      this.scrollbarHover = inScrollbar;
      this.requestRender();
    }
    if (inScrollbar) {
      this.canvas.style.cursor = 'grab';
      return;
    }
    if (y < L.rulerH) {
      this.canvas.style.cursor = 'text';
      return;
    }
    const hit = this.hitTestClip(x, y);
    if (!hit) {
      this.canvas.style.cursor = 'default';
      return;
    }
    // C2: Alt 悬停音频/波形片段 → 增益拖拽光标
    if (this.altKeyHeld && (hit.track.kind === 'audio' || hit.track.kind === 'waveform')) {
      this.canvas.style.cursor = 'ns-resize';
      return;
    }
    const clipX = timeToX(hit.clip.start_sec, L);
    const clipW = hit.clip.duration_sec * L.zoom;
    if (x - clipX < TRIM_HANDLE_PX || clipX + clipW - x < TRIM_HANDLE_PX) {
      this.canvas.style.cursor = 'ew-resize';
    } else {
      this.canvas.style.cursor = 'grab';
    }
  }

  // ── zoom / scroll ─────────────────────────────────────
  private onWheel = (e: WheelEvent) => {
    if (this.disposed) return;
    e.preventDefault();
    const L = this.layout();
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;

    if (e.ctrlKey || e.metaKey || e.altKey) {
      // Ctrl/Cmd/Alt+wheel → zoom to cursor
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const timeAtCursor = xToTime(x, L);
      this.zoom = clamp(this.zoom * factor, MIN_ZOOM, MAX_ZOOM);
      this.scrollX = Math.max(0, timeAtCursor * this.zoom - (x - L.headerW));
    } else if (e.shiftKey) {
      // Shift+wheel → horizontal scroll (browser puts delta in deltaX for Shift+wheel)
      const dx = e.deltaX || e.deltaY;
      this.scrollX = Math.max(0, this.scrollX + dx * 3);
    } else {
      // Plain wheel / trackpad pan: deltaX → horizontal, deltaY → vertical.
      // A mouse wheel emits deltaX=0 (vertical only); a trackpad two-finger
      // swipe emits real deltaX/deltaY, so horizontal swipes scroll sideways.
      if (e.deltaX) this.scrollX = Math.max(0, this.scrollX + e.deltaX);
      if (e.deltaY) this.scrollY = Math.max(0, this.scrollY + e.deltaY);
    }
    this.clampScroll();
    this.requestRender();
  };

  private clampScroll() {
    const tl = useTimelineStore.getState().timeline;
    const L = this.layout();
    const maxX = Math.max(0, tl.duration_sec * L.zoom - (L.width - L.headerW));
    const maxY = Math.max(0, tl.tracks.length * L.trackH - (L.height - L.rulerH - L.scrollbarH));
    this.scrollX = clamp(this.scrollX, 0, maxX);
    this.scrollY = clamp(this.scrollY, 0, maxY);
  }

  /** Horizontal scrollbar geometry for the current viewport & timeline. */
  private hScrollbar() {
    const L = this.layout();
    const contentW = useTimelineStore.getState().timeline.duration_sec * L.zoom;
    return scrollbarGeom(L, contentW);
  }

  /** Map a mouse X position to scrollX while dragging the scrollbar thumb. */
  private applyScrollbarScroll(mouseX: number) {
    const g = this.hScrollbar();
    const free = g.trackW - g.thumbW;
    if (free <= 0 || g.maxX <= 0) return;
    const thumbX = mouseX - this.drag.scrollbarGrabOffset - g.trackX;
    this.scrollX = clamp((thumbX / free) * g.maxX, 0, g.maxX);
    this.clampScroll();
    this.requestRender();
  }

  // ── public API ───────────────────────────────────────
  zoomIn() { this.setZoom(this.zoom * 1.3); }
  zoomOut() { this.setZoom(this.zoom / 1.3); }
  zoomToFit(durationSec: number) {
    if (durationSec <= 0) return;
    const L = this.layout();
    const avail = L.width - L.headerW - 40;
    this.setZoom(avail / durationSec);
  }
  /** Set zoom so that `seconds` worth of timeline fills the viewport. */
  zoomPreset(seconds: number) {
    const L = this.layout();
    const avail = L.width - L.headerW - 40;
    this.setZoom(avail / Math.max(0.1, seconds));
  }
  private setZoom(z: number) {
    const L = this.layout();
    const centerTime = xToTime(L.headerW + (L.width - L.headerW) / 2, L);
    this.zoom = clamp(z, MIN_ZOOM, MAX_ZOOM);
    this.scrollX = Math.max(0, centerTime * this.zoom - (L.width - L.headerW) / 2);
    this.clampScroll();
    this.requestRender();
  }

  addMarkerAtPlayhead() {
    const t = usePreviewStore.getState().currentTimeSec;
    if (!this.markers.some((m) => Math.abs(m.time - t) < 0.01)) {
      this.markers.push({ time: t });
      this.markers.sort((a, b) => a.time - b.time);
      this.requestRender();
      this.onMarkersChange?.(this.markers);
    }
  }

  /** M8: 从外部（store/项目加载）整体设置标记列表。 */
  setMarkers(markers: Marker[]) {
    this.markers = (markers ?? [])
      .map((m) => ({ time: Math.max(0, m.time), name: m.name ?? '' }))
      .sort((a, b) => a.time - b.time);
    this.requestRender();
  }

  /** M8: 重命名标记（按 time 定位，不改变时间点）。 */
  renameMarker(time: number, name: string) {
    const m = this.markers.find((x) => Math.abs(x.time - time) < 0.01);
    if (!m) return false;
    m.name = name ?? '';
    this.requestRender();
    this.onMarkersChange?.(this.markers);
    return true;
  }

  /** Remove the marker nearest to the playhead (within 0.5s). */
  removeMarkerNearest() {
    const t = usePreviewStore.getState().currentTimeSec;
    let bestIdx = -1;
    let bestDist = 0.5;
    this.markers.forEach((m, i) => {
      const d = Math.abs(m.time - t);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    });
    if (bestIdx >= 0) {
      this.markers.splice(bestIdx, 1);
      this.requestRender();
      this.onMarkersChange?.(this.markers);
    }
  }

  clearMarkers() {
    this.markers = [];
    this.requestRender();
    this.onMarkersChange?.(this.markers);
  }

  /** Jump to the next marker after the playhead. */
  jumpToNextMarker() {
    const t = usePreviewStore.getState().currentTimeSec;
    const next = this.markers.find((m) => m.time > t + 0.01);
    if (next) usePreviewStore.getState().setCurrentTime(next.time);
  }

  /** Jump to the previous marker before the playhead. */
  jumpToPrevMarker() {
    const t = usePreviewStore.getState().currentTimeSec;
    const prev = [...this.markers].reverse().find((m) => m.time < t - 0.01);
    if (prev) usePreviewStore.getState().setCurrentTime(prev.time);
  }

  /** Jump to the next clip edge (start or end). */
  jumpToNextEdit() {
    const t = usePreviewStore.getState().currentTimeSec;
    const tl = useTimelineStore.getState().timeline;
    let best = Infinity;
    for (const tr of tl.tracks) {
      for (const c of tr.clips) {
        if (c.start_sec > t + 0.001 && c.start_sec < best) best = c.start_sec;
        const end = c.start_sec + c.duration_sec;
        if (end > t + 0.001 && end < best) best = end;
      }
    }
    if (best < Infinity) usePreviewStore.getState().setCurrentTime(best);
  }

  /** Jump to the previous clip edge. */
  jumpToPrevEdit() {
    const t = usePreviewStore.getState().currentTimeSec;
    const tl = useTimelineStore.getState().timeline;
    let best = -Infinity;
    for (const tr of tl.tracks) {
      for (const c of tr.clips) {
        if (c.start_sec < t - 0.001 && c.start_sec > best) best = c.start_sec;
        const end = c.start_sec + c.duration_sec;
        if (end < t - 0.001 && end > best) best = end;
      }
    }
    if (best > -Infinity) usePreviewStore.getState().setCurrentTime(best);
  }

  get markerCount() {
    return this.markers.length;
  }

  /**
   * Handle an asset dropped from the asset panel.
   * Computes the drop time/track from canvas coordinates and inserts a clip.
   */
  dropAssetAt(
    canvasX: number,
    canvasY: number,
    asset: { id: string; kind: string; filename: string; duration: number },
  ) {
    const L = this.layout();
    const store = useTimelineStore.getState();
    const dropTime = Math.max(0, xToTime(canvasX, L));
    const dropTrackIdx = yToTrackIndex(canvasY, L);

    const clipKind = normalizeClipKind(asset.kind);
    const clipDuration = asset.duration || 5;
    // Snap drop time to a sensible grid (0.1s)
    const startSec = Math.round(dropTime * 10) / 10;

    // Find existing tracks of the same kind
    const tracks = store.timeline.tracks;
    const sameKindTracks = tracks.filter((t) => t.kind === clipKind);

    // Prefer the dropped-on track if its kind matches
    let targetTrack: Track | undefined =
      dropTrackIdx >= 0 && dropTrackIdx < tracks.length && tracks[dropTrackIdx].kind === clipKind
        ? tracks[dropTrackIdx]
        : undefined;

    // Check overlap on the preferred track
    const hasOverlap = (track: typeof tracks[number]) =>
      track.clips.some((c) => c.start_sec < startSec + clipDuration && c.start_sec + c.duration_sec > startSec);

    if (targetTrack && hasOverlap(targetTrack)) {
      // Overlap on dropped track → append after last clip
      const lastEnd = targetTrack.clips.reduce((m, c) => Math.max(m, c.start_sec + c.duration_sec), 0);
      useHistoryStore.getState().pushState(store.timeline, 'drop');
      const clipId = store.addClip(targetTrack.id, {
        kind: clipKind,
        asset_id: asset.id,
        start_sec: lastEnd,
        duration_sec: clipDuration,
        metadata: { title: asset.filename },
      });
      if (clipId) useSelectionStore.getState().selectClip(clipId);
      this.requestRender();
      return;
    }

    if (!targetTrack) {
      // No preferred track or no overlap on it → try ANY same-kind track without overlap
      targetTrack = sameKindTracks.find((t) => !hasOverlap(t));
      if (!targetTrack) {
        // All same-kind tracks have overlap → create new track
        // Insert at drop position or below existing same-kind tracks
        const insertIdx = dropTrackIdx >= 0 && dropTrackIdx <= tracks.length
          ? dropTrackIdx + 1
          : (sameKindTracks.length > 0 ? tracks.indexOf(sameKindTracks[sameKindTracks.length - 1]) + 1 : tracks.length);
        const tid = store.addTrack(clipKind, undefined, insertIdx);
        targetTrack = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
        if (!targetTrack) return;
      }
    }

    useHistoryStore.getState().pushState(store.timeline, 'drop');
    const clipId = store.addClip(targetTrack.id, {
      kind: clipKind,
      asset_id: asset.id,
      start_sec: startSec,
      duration_sec: clipDuration,
      metadata: { title: asset.filename },
    });
    if (clipId) {
      useSelectionStore.getState().selectClip(clipId);
    } else {
      useSelectionStore.getState().selectTrack(targetTrack.id);
    }
    this.requestRender();
  }

  scrollToPlayhead() {
    const L = this.layout();
    const t = usePreviewStore.getState().currentTimeSec;
    const x = timeToX(t, L);
    if (x < L.headerW || x > L.width) {
      this.scrollX = Math.max(0, t * this.zoom - (L.width - L.headerW) / 3);
      this.clampScroll();
      this.requestRender();
    }
  }

  // ── event binding ────────────────────────────────────
  private localPos(e: PointerEvent | MouseEvent) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    this.lastMouse = { x, y };
    return { x, y };
  }

  private bindPointerEvents() {
    this.canvas.addEventListener('pointerdown', this.onPointerDown);
    this.canvas.addEventListener('pointermove', this.onPointerMove);
    this.canvas.addEventListener('pointerup', this.onPointerUp);
    this.canvas.addEventListener('pointercancel', this.onPointerCancel);
    // C2: 跟踪 Alt 键（悬停音频片段时切换增益光标）
    this._keyHandler = (e: KeyboardEvent) => {
      if (e.key === 'Alt') {
        this.altKeyHeld = e.type === 'keydown';
        this.requestRender();
      }
    };
    window.addEventListener('keydown', this._keyHandler);
    window.addEventListener('keyup', this._keyHandler);
    this._keyUnsubs = [
      () => window.removeEventListener('keydown', this._keyHandler!),
      () => window.removeEventListener('keyup', this._keyHandler!),
    ];
  }
  private removePointerEvents() {
    this.canvas.removeEventListener('pointerdown', this.onPointerDown);
    this.canvas.removeEventListener('pointermove', this.onPointerMove);
    this.canvas.removeEventListener('pointerup', this.onPointerUp);
    this.canvas.removeEventListener('pointercancel', this.onPointerCancel);
    this._keyUnsubs?.forEach((u) => u());
    this._keyUnsubs = null;
  }
  private bindWheelEvent() {
    this.canvas.addEventListener('wheel', this.onWheel, { passive: false });
  }
}
