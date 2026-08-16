// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock ResizeObserver (jsdom doesn't implement it)
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', MockResizeObserver);

// Mock canvas 2D context (jsdom doesn't implement it)
const mockCtx = {
  setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(),
  beginPath: vi.fn(), closePath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(),
  arc: vi.fn(), arcTo: vi.fn(), fill: vi.fn(), stroke: vi.fn(), rect: vi.fn(),
  fillText: vi.fn(), strokeText: vi.fn(), measureText: vi.fn(() => ({ width: 0 })),
  drawImage: vi.fn(), save: vi.fn(), restore: vi.fn(), clip: vi.fn(),
  translate: vi.fn(), rotate: vi.fn(), scale: vi.fn(), createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  setLineDash: vi.fn(), createPattern: vi.fn(),
  canvas: { width: 0, height: 0 },
};
vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(mockCtx as any);

import { TimelineEngine } from './TimelineEngine';
import { DEFAULT_ZOOM, HEADER_W, RULER_H, TRACK_H, MIN_ZOOM, MAX_ZOOM, SCROLLBAR_H, makeLayout, scrollbarGeom } from './types';
import { useTimelineStore } from '@/stores/timelineStore';

vi.mock('@/stores/timelineStore', () => ({
  useTimelineStore: Object.assign(
    vi.fn((sel: any) => sel({ timeline: { duration_sec: 10, tracks: [{ id: 'v1', name: 'V1', kind: 'video', index: 0, locked: false, muted: false, clips: [] }] } })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ timeline: { duration_sec: 10, tracks: [{ id: 'v1', name: 'V1', kind: 'video', index: 0, locked: false, muted: false, clips: [] }] } }),
    },
  ),
}));

vi.mock('@/stores/selectionStore', () => ({
  useSelectionStore: Object.assign(
    vi.fn((sel: any) => sel({ selectedClipIds: new Set(), selectedTrackId: null })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ selectedClipIds: new Set(), selectedTrackId: null, selectTrack: vi.fn(), selectClip: vi.fn(), toggleClip: vi.fn(), clearSelection: vi.fn() }),
    },
  ),
}));

vi.mock('@/stores/previewStore', () => ({
  usePreviewStore: Object.assign(
    vi.fn((sel: any) => sel({ currentTimeSec: 0, playing: false })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ currentTimeSec: 0, playing: false, setCurrentTime: vi.fn(), setPlaying: vi.fn() }),
    },
  ),
}));

vi.mock('@/stores/settingsStore', () => ({
  useSettingsStore: Object.assign(
    vi.fn((sel: any) => sel({ snapEnabled: false, snapThresholdPx: 5 })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ snapEnabled: false, snapThresholdPx: 5 }),
    },
  ),
}));

vi.mock('@/stores/historyStore', () => ({
  useHistoryStore: Object.assign(
    vi.fn(() => ({})),
    { subscribe: vi.fn(() => () => {}), getState: () => ({ push: vi.fn() }) },
  ),
}));

vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: {
    onChange: vi.fn(() => () => {}),
    hasRealMedia: vi.fn(() => false),
    captureThumbnail: vi.fn(() => Promise.resolve(null)),
  },
}));

function createCanvas(w = 1000, h = 400) {
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  const parent = canvas.parentElement!;
  vi.spyOn(parent, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, width: w, height: h,
    top: 0, left: 0, bottom: h, right: w,
    toJSON: () => {},
  } as DOMRect);
  return canvas;
}

function mkWheel(opts: { deltaY?: number; deltaX?: number; ctrlKey?: boolean; shiftKey?: boolean; clientX?: number }) {
  return new WheelEvent('wheel', {
    deltaY: opts.deltaY ?? 0, deltaX: opts.deltaX ?? 0,
    ctrlKey: opts.ctrlKey ?? false, shiftKey: opts.shiftKey ?? false,
    clientX: opts.clientX ?? 500, clientY: 200, bubbles: true,
  });
}

describe('TimelineEngine clampScroll', () => {
  let engine: TimelineEngine;
  let canvas: HTMLCanvasElement;
  const CSS_W = 1000;
  const CSS_H = 400;
  const DURATION = 10;
  const NUM_TRACKS = 1;

  beforeEach(() => {
    canvas = createCanvas(CSS_W, CSS_H);
    engine = new TimelineEngine(canvas);
    engine.zoom = DEFAULT_ZOOM;
    engine.scrollX = 0;
    engine.scrollY = 0;
  });

  afterEach(() => { engine.dispose(); canvas.remove(); });

  it('scrollX clamped to [0, maxX] — excess scrolls back to maxX', () => {
    const maxX = Math.max(0, DURATION * DEFAULT_ZOOM - (CSS_W - HEADER_W));
    engine.scrollX = maxX + 999;
    (engine as any).clampScroll();
    expect(engine.scrollX).toBe(maxX);
  });

  it('scrollX stays 0 when trying negative', () => {
    engine.scrollX = -100;
    (engine as any).clampScroll();
    expect(engine.scrollX).toBe(0);
  });

  it('scrollY clamped to [0, maxY]', () => {
    const maxY = Math.max(0, NUM_TRACKS * TRACK_H - (CSS_H - RULER_H));
    engine.scrollY = maxY + 999;
    (engine as any).clampScroll();
    expect(engine.scrollY).toBe(maxY);
  });

  it('scrollX/scrollY stay 0 when content fits viewport (max=0)', () => {
    engine.scrollX = 50;
    engine.scrollY = 50;
    // Override store to have a very short timeline that fits in viewport
    const origGetState = (useTimelineStore as any).getState;
    (useTimelineStore as any).getState = () => ({
      timeline: { duration_sec: 0.1, tracks: [] },
    });
    (engine as any).clampScroll();
    expect(engine.scrollX).toBe(0);
    expect(engine.scrollY).toBe(0);
    (useTimelineStore as any).getState = origGetState;
  });

  it('onWheel respects clamp — cannot scroll past content', () => {
    const maxX = Math.max(0, DURATION * DEFAULT_ZOOM - (CSS_W - HEADER_W));
    // Shift+wheel a lot to exceed bounds
    for (let i = 0; i < 200; i++) {
      (engine as any).onWheel(mkWheel({ shiftKey: true, deltaX: 100 }));
    }
    expect(engine.scrollX).toBeLessThanOrEqual(maxX);
    expect(engine.scrollX).toBeGreaterThanOrEqual(0);
  });

  it('scrollbar drag: grabbing the thumb and dragging right scrolls the timeline', () => {
    // Zoom in so content overflows the viewport and the scrollbar becomes active
    engine.zoom = 200;
    engine.scrollX = 0;
    (canvas as any).setPointerCapture = vi.fn();

    const g = (engine as any).hScrollbar();
    expect(g.maxX).toBeGreaterThan(0);

    const y = CSS_H - SCROLLBAR_H / 2; // vertical middle of the scrollbar
    const grabX = g.thumbX + g.thumbW / 2;

    (engine as any).onPointerDown(new PointerEvent('pointerdown', { clientX: grabX, clientY: y, bubbles: true }));
    expect((engine as any).drag.mode).toBe('scrollbar');

    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: grabX + 200, clientY: y, bubbles: true }));
    expect(engine.scrollX).toBeGreaterThan(0);
    expect(engine.scrollX).toBeLessThanOrEqual(g.maxX);

    (engine as any).onPointerUp(new PointerEvent('pointerup', { clientX: grabX + 200, clientY: y, bubbles: true }));
    expect((engine as any).drag.mode).toBe('none');
  });
});

describe('scrub autoscroll', () => {
  let engine: TimelineEngine;
  let canvas: HTMLCanvasElement;
  const CSS_W = 1000;
  const CSS_H = 400;

  beforeEach(() => {
    canvas = createCanvas(CSS_W, CSS_H);
    engine = new TimelineEngine(canvas);
    engine.zoom = 200; // zoom in so content overflows the viewport
    engine.scrollX = 0;
    engine.scrollY = 0;
    (canvas as any).setPointerCapture = vi.fn();
  });

  afterEach(() => { engine.dispose(); canvas.remove(); });

  it('scrub: dragging playhead past the right edge autoscrolls the timeline', () => {
    (engine as any).onPointerDown(new PointerEvent('pointerdown', { clientX: 500, clientY: 10, bubbles: true }));
    expect((engine as any).drag.mode).toBe('scrub');

    const maxX = Math.max(0, 10 * 200 - (CSS_W - HEADER_W));
    expect(maxX).toBeGreaterThan(0);
    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: 1000, clientY: 10, bubbles: true }));
    expect(engine.scrollX).toBeGreaterThan(0);
    expect(engine.scrollX).toBeLessThanOrEqual(maxX);
  });

  it('scrub: moving back into the viewport stops scrolling', () => {
    (engine as any).onPointerDown(new PointerEvent('pointerdown', { clientX: 500, clientY: 10, bubbles: true }));
    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: 1000, clientY: 10, bubbles: true }));
    const s1 = engine.scrollX;
    expect(s1).toBeGreaterThan(0);
    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: 500, clientY: 10, bubbles: true }));
    expect(engine.scrollX).toBe(s1);
  });

  it('scrub: dragging past the left edge scrolls back toward 0', () => {
    engine.scrollX = 200;
    (engine as any).onPointerDown(new PointerEvent('pointerdown', { clientX: 500, clientY: 10, bubbles: true }));
    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: 100, clientY: 10, bubbles: true }));
    expect(engine.scrollX).toBeLessThan(200);
    expect(engine.scrollX).toBeGreaterThanOrEqual(0);
  });

  it('scrub: no autoscroll when content fits viewport (maxX=0), no NaN', () => {
    engine.zoom = DEFAULT_ZOOM; // 10 * 60 = 600 < viewport width → maxX = 0
    engine.scrollX = 0;
    (engine as any).onPointerDown(new PointerEvent('pointerdown', { clientX: 500, clientY: 10, bubbles: true }));
    (engine as any).onPointerMove(new PointerEvent('pointermove', { clientX: 1000, clientY: 10, bubbles: true }));
    expect(engine.scrollX).toBe(0);
    expect(Number.isNaN(engine.scrollX)).toBe(false);
  });
});

describe('scrollbarGeom', () => {
  it('thumb starts at trackX when scrollX=0 and reaches the right edge at scrollX=maxX', () => {
    const contentW = 2000; // wider than the 1000px viewport
    const g0 = scrollbarGeom(makeLayout(1000, 400, 60, 0, 0), contentW);
    expect(g0.maxX).toBe(contentW - (1000 - HEADER_W));
    expect(g0.thumbX).toBe(g0.trackX);

    const g1 = scrollbarGeom(makeLayout(1000, 400, 60, g0.maxX, 0), contentW);
    expect(g1.thumbX + g1.thumbW).toBeCloseTo(g1.trackX + g1.trackW, 5);
  });

  it('thumb fills the track when all content is visible (maxX=0)', () => {
    const g = scrollbarGeom(makeLayout(1000, 400, 60, 0, 0), 100);
    expect(g.maxX).toBe(0);
    expect(g.thumbW).toBe(g.trackW);
  });
});
