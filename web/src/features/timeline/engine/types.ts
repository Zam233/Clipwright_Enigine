/**
 * Timeline engine shared types & coordinate helpers.
 * All rendering is done in CSS pixels; the canvas is scaled by devicePixelRatio.
 */
import type { Clip } from '@/types/timeline';

export interface TimelineLayout {
  /** Canvas CSS width/height */
  width: number;
  height: number;
  /** Track header column width */
  headerW: number;
  /** Time ruler height */
  rulerH: number;
  /** Height of a single track lane */
  trackH: number;
  /** Pixels per second */
  zoom: number;
  /** Horizontal scroll (px, in clip-area space) */
  scrollX: number;
  /** Vertical scroll (px) */
  scrollY: number;
  /** Horizontal scrollbar height (reserved at the bottom) */
  scrollbarH: number;
}

export const HEADER_W = 152;
export const RULER_H = 30;
export const TRACK_H = 48;
export const SCROLLBAR_H = 12;

export interface Marker {
  time: number;
  name?: string;
}
export const MIN_ZOOM = 4;
export const MAX_ZOOM = 600;
export const DEFAULT_ZOOM = 60;

export function makeLayout(
  width: number,
  height: number,
  zoom: number,
  scrollX: number,
  scrollY: number,
): TimelineLayout {
  return { width, height, headerW: HEADER_W, rulerH: RULER_H, trackH: TRACK_H, zoom, scrollX, scrollY, scrollbarH: SCROLLBAR_H };
}

// ── Coordinate transforms ──────────────────────────────
export const timeToX = (t: number, L: TimelineLayout) =>
  L.headerW + t * L.zoom - L.scrollX;

export const xToTime = (x: number, L: TimelineLayout) =>
  (x - L.headerW + L.scrollX) / L.zoom;

export const trackToY = (index: number, L: TimelineLayout) =>
  L.rulerH + index * L.trackH - L.scrollY;

export const yToTrackIndex = (y: number, L: TimelineLayout) =>
  Math.floor((y - L.rulerH + L.scrollY) / L.trackH);

// ── Drag interaction state ─────────────────────────────
export type DragMode =
  | 'none'
  | 'scrub'
  | 'marquee'
  | 'move-clip'
  | 'trim-start'
  | 'trim-end'
  | 'pan'
  | 'scrollbar';

export interface DragState {
  mode: DragMode;
  startMouse: { x: number; y: number };
  /** Time under the mouse when drag began */
  startTime: number;
  /** Clips being moved (id -> original clip) */
  origClips: Map<string, Clip>;
  /** Current horizontal drag delta in seconds */
  deltaTime: number;
  /** Current vertical drag delta in track indices */
  deltaTrack: number;
  /** Clip being trimmed */
  trimClipId: string | null;
  trimOrig: Clip | null;
  /** Marquee rect in screen px */
  marquee: { x0: number; y0: number; x1: number; y1: number } | null;
  /** Active snap guide screen X (for rendering) */
  snapX: number | null;
  /** Whether we pushed a history snapshot for this drag */
  historyPushed: boolean;
  /** Offset from the scrollbar thumb's left edge to the grab point */
  scrollbarGrabOffset: number;
}

export function makeDragState(): DragState {
  return {
    mode: 'none',
    startMouse: { x: 0, y: 0 },
    startTime: 0,
    origClips: new Map(),
    deltaTime: 0,
    deltaTrack: 0,
    trimClipId: null,
    trimOrig: null,
    marquee: null,
    snapX: null,
    historyPushed: false,
    scrollbarGrabOffset: 0,
  };
}

/** Hit-test edge zone width in px for trim handles */
export const TRIM_HANDLE_PX = 7;

// ── Horizontal scrollbar geometry ─────────────────────
export interface ScrollbarGeom {
  /** Scrollbar track (full scrollable area) */
  trackX: number;
  trackW: number;
  /** Thumb (draggable knob) */
  thumbX: number;
  thumbW: number;
  /** Vertical position & height */
  y: number;
  h: number;
  /** Max horizontal scroll (px) */
  maxX: number;
}

/**
 * Compute the horizontal scrollbar geometry for a given layout and content
 * width (timeline duration in px). The scrollbar spans the content area to
 * the right of the track headers, pinned to the bottom of the canvas.
 */
export function scrollbarGeom(L: TimelineLayout, contentW: number): ScrollbarGeom {
  const trackX = L.headerW;
  const trackW = Math.max(0, L.width - L.headerW);
  const maxX = Math.max(0, contentW - trackW);
  const minThumb = 32;
  const ratio = contentW > 0 ? Math.min(1, trackW / contentW) : 1;
  const thumbW = Math.max(minThumb, Math.round(trackW * ratio));
  const thumbX = maxX > 0
    ? trackX + (L.scrollX / maxX) * Math.max(0, trackW - thumbW)
    : trackX;
  return { trackX, trackW, thumbX, thumbW, y: L.height - L.scrollbarH, h: L.scrollbarH, maxX };
}
