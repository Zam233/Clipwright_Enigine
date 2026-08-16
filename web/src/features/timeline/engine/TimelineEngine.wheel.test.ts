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
import { DEFAULT_ZOOM, HEADER_W, RULER_H, TRACK_H } from './types';

vi.mock('@/stores/timelineStore', () => ({
  useTimelineStore: Object.assign(
    vi.fn((sel: any) => sel({ timeline: { duration_sec: 60, tracks: Array.from({ length: 20 }, (_, i) => ({ id: `v${i}`, name: `V${i}`, kind: 'video', index: i, locked: false, muted: false, clips: [] })) } })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ timeline: { duration_sec: 60, tracks: Array.from({ length: 20 }, (_, i) => ({ id: `v${i}`, name: `V${i}`, kind: 'video', index: i, locked: false, muted: false, clips: [] })) } }),
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

function mkWheel(opts: { deltaY?: number; deltaX?: number; ctrlKey?: boolean; shiftKey?: boolean; metaKey?: boolean; altKey?: boolean; clientX?: number }) {
  return new WheelEvent('wheel', {
    deltaY: opts.deltaY ?? 0, deltaX: opts.deltaX ?? 0,
    ctrlKey: opts.ctrlKey ?? false, shiftKey: opts.shiftKey ?? false,
    metaKey: opts.metaKey ?? false, altKey: opts.altKey ?? false, clientX: opts.clientX ?? 500, clientY: 200, bubbles: true,
  });
}

describe('TimelineEngine onWheel modifier mapping', () => {
  let engine: TimelineEngine;
  let canvas: HTMLCanvasElement;

  beforeEach(() => {
    canvas = createCanvas();
    engine = new TimelineEngine(canvas);
    engine.zoom = DEFAULT_ZOOM;
    engine.scrollX = 0;
    engine.scrollY = 0;
  });

  afterEach(() => { engine.dispose(); canvas.remove(); });

  it('Shift+wheel deltaX=100 → scrollX increases (was dead before fix)', () => {
    const prev = engine.scrollX;
    (engine as any).onWheel(mkWheel({ shiftKey: true, deltaX: 100 }));
    expect(engine.scrollX).toBeGreaterThan(prev);
  });

  it('Shift+wheel with only deltaY falls back to deltaY', () => {
    const prev = engine.scrollX;
    (engine as any).onWheel(mkWheel({ shiftKey: true, deltaY: 60, deltaX: 0 }));
    expect(engine.scrollX).toBeGreaterThan(prev);
  });

  it('Plain wheel → zoom unchanged, scrollY increases', () => {
    const prevZoom = engine.zoom;
    (engine as any).onWheel(mkWheel({ deltaY: 50 }));
    expect(engine.zoom).toBe(prevZoom);
    expect(engine.scrollY).toBeGreaterThan(0);
  });

  it('Plain trackpad horizontal swipe (deltaX only) → scrollX increases, scrollY unchanged', () => {
    const prevZoom = engine.zoom;
    const prevScrollY = engine.scrollY;
    (engine as any).onWheel(mkWheel({ deltaX: 80, deltaY: 0 }));
    expect(engine.zoom).toBe(prevZoom);
    expect(engine.scrollX).toBeGreaterThan(0);
    expect(engine.scrollY).toBe(prevScrollY);
  });

  it('Ctrl+wheel deltaY<0 → zoom increases (zoom in)', () => {
    const prevZoom = engine.zoom;
    (engine as any).onWheel(mkWheel({ ctrlKey: true, deltaY: -100 }));
    expect(engine.zoom).toBeGreaterThan(prevZoom);
  });

  it('Ctrl+wheel deltaY>0 → zoom decreases (zoom out)', () => {
    const prevZoom = engine.zoom;
    (engine as any).onWheel(mkWheel({ ctrlKey: true, deltaY: 100 }));
    expect(engine.zoom).toBeLessThan(prevZoom);
  });

  it('Cmd+wheel (metaKey) → also zooms', () => {
    const prevZoom = engine.zoom;
    (engine as any).onWheel(mkWheel({ metaKey: true, deltaY: -100 }));
    expect(engine.zoom).toBeGreaterThan(prevZoom);
  });

  it('Alt+wheel → also zooms', () => {
    const prevZoom = engine.zoom;
    (engine as any).onWheel(mkWheel({ altKey: true, deltaY: -100 }));
    expect(engine.zoom).toBeGreaterThan(prevZoom);
  });

  it('e.preventDefault() is called', () => {
    const e = mkWheel({ deltaY: 50 });
    const spy = vi.spyOn(e, 'preventDefault');
    (engine as any).onWheel(e);
    expect(spy).toHaveBeenCalled();
  });
});
