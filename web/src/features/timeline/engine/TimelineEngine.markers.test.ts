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
import { DEFAULT_ZOOM, HEADER_W } from './types';

const EMPTY_TL = { duration_sec: 60, tracks: [] };
vi.mock('@/stores/timelineStore', () => ({
  useTimelineStore: Object.assign(
    vi.fn((sel: any) => sel({ timeline: EMPTY_TL })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ timeline: EMPTY_TL }),
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

describe('TimelineEngine markers (M8 persistence + naming)', () => {
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

  it('setMarkers replaces and sorts the marker list', () => {
    engine.setMarkers([{ time: 12 }, { time: 3, name: '片头' }]);
    expect(engine.markerCount).toBe(2);
    expect(engine.markers[0].time).toBe(3);
    expect(engine.markers[0].name).toBe('片头');
    expect(engine.markers[1].time).toBe(12);
  });

  it('setMarkers tolerates undefined/null input and negative times', () => {
    engine.setMarkers(undefined as never);
    expect(engine.markerCount).toBe(0);
    engine.setMarkers([{ time: -5 }, { time: 2 }]);
    expect(engine.markers[0].time).toBe(0);
  });

  it('addMarkerAtPlayhead fires onMarkersChange with the new list', () => {
    const onChange = vi.fn();
    engine.onMarkersChange = onChange;
    vi.spyOn(usePreviewStoreMock(), 'getState').mockReturnValue({ currentTimeSec: 7, setCurrentTime: vi.fn(), setPlaying: vi.fn() } as never);
    engine.addMarkerAtPlayhead();
    expect(engine.markerCount).toBe(1);
    expect(onChange).toHaveBeenCalledWith([{ time: 7 }]);
  });

  it('renameMarker updates the name and fires onMarkersChange', () => {
    const onChange = vi.fn();
    engine.onMarkersChange = onChange;
    engine.setMarkers([{ time: 5 }]);
    const ok = engine.renameMarker(5, '高潮点');
    expect(ok).toBe(true);
    expect(engine.markers[0].name).toBe('高潮点');
    expect(onChange).toHaveBeenCalledTimes(1);
    // 未知时间 → false 且不触发
    expect(engine.renameMarker(99, 'x')).toBe(false);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('removeMarkerNearest / clearMarkers fire onMarkersChange', () => {
    const onChange = vi.fn();
    engine.onMarkersChange = onChange;
    engine.setMarkers([{ time: 1 }, { time: 2 }]);
    vi.spyOn(usePreviewStoreMock(), 'getState').mockReturnValue({ currentTimeSec: 1.05, setCurrentTime: vi.fn(), setPlaying: vi.fn() } as never);
    engine.removeMarkerNearest();
    expect(engine.markerCount).toBe(1);
    expect(onChange).toHaveBeenCalledTimes(1);
    engine.clearMarkers();
    expect(engine.markerCount).toBe(0);
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});

// 引用被 mock 的 previewStore，供上面 spy 使用
import { usePreviewStore } from '@/stores/previewStore';
function usePreviewStoreMock() {
  return usePreviewStore as unknown as { getState: () => { currentTimeSec: number; setCurrentTime: (t: number) => void; setPlaying: (p: boolean) => void } };
}
void HEADER_W;
