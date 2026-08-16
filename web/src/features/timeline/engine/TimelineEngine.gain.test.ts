// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', MockResizeObserver);

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
import { createDefaultClip } from '@/types/timeline';

// timelineStore 提供一条 audio 轨道 + 一个片段，并记录 updateClip 调用
const updateClipMock = vi.fn();
const pushMock = vi.fn();
const selectClipMock = vi.fn();
const selectTrackMock = vi.fn();

const AUDIO_CLIP = createDefaultClip({
  id: 'clip1', kind: 'audio', track_id: 'a1', start_sec: 0, duration_sec: 5, volume: 1,
});

const TL = {
  duration_sec: 60,
  tracks: [{
    id: 'a1', name: 'A1', kind: 'audio', index: 0, locked: false, muted: false,
    clips: [AUDIO_CLIP],
  }],
};

vi.mock('@/stores/timelineStore', () => ({
  useTimelineStore: Object.assign(
    vi.fn((sel: any) => sel({ timeline: TL })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ timeline: TL, updateClip: updateClipMock }),
    },
  ),
}));

vi.mock('@/stores/selectionStore', () => ({
  useSelectionStore: Object.assign(
    vi.fn((sel: any) => sel({ selectedClipIds: [], selectedTrackId: null })),
    {
      subscribe: vi.fn(() => () => {}),
      getState: () => ({ selectedClipIds: [], selectedTrackId: null, selectClip: selectClipMock, selectTrack: selectTrackMock }),
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
    { subscribe: vi.fn(() => () => {}), getState: () => ({ pushState: pushMock }) },
  ),
}));

vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: {
    onChange: vi.fn(() => () => {}),
    hasRealMedia: vi.fn(() => false),
    captureThumbnail: vi.fn(() => Promise.resolve(null)),
    getCachedWaveform: vi.fn(() => null),
    ensureWaveform: vi.fn(),
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

function mkPointer(type: string, opts: { x: number; y: number; altKey?: boolean; button?: number }) {
  return new PointerEvent(type, {
    clientX: opts.x, clientY: opts.y,
    altKey: opts.altKey ?? false, button: opts.button ?? 0, bubbles: true, pointerId: 1,
  } as PointerEventInit);
}

describe('TimelineEngine C2 (Alt 拖拽增益)', () => {
  let engine: TimelineEngine;
  let canvas: HTMLCanvasElement;

  beforeEach(() => {
    vi.clearAllMocks();
    canvas = createCanvas();
    // jsdom 没有 setPointerCapture / releasePointerCapture
    canvas.setPointerCapture = vi.fn();
    canvas.releasePointerCapture = vi.fn();
    engine = new TimelineEngine(canvas);
    engine.zoom = DEFAULT_ZOOM;
    engine.scrollX = 0;
    engine.scrollY = 0;
    updateClipMock.mockImplementation((id: string, updates: { volume?: number }) => {
      if (updates.volume !== undefined) AUDIO_CLIP.volume = updates.volume;
    });
  });

  afterEach(() => { engine.dispose(); canvas.remove(); });

  it('Alt+pointerdown 音频片段 → gain 模式 + 选中片段', () => {
    // 片段起点 x = HEADER_W + 0*zoom；点在片段中部
    const x = HEADER_W + DEFAULT_ZOOM * 1;
    const y = RULER_H + TRACK_H * 0 + TRACK_H / 2;
    (engine as any).onPointerDown(mkPointer('pointerdown', { x, y, altKey: true }));
    expect((engine as any).drag.mode).toBe('gain');
    expect((engine as any).drag.gainClipId).toBe('clip1');
    expect(selectClipMock).toHaveBeenCalledWith('clip1', false);
    expect(pushMock).toHaveBeenCalled(); // history push
  });

  it('向上拖拽 → 增益提高；向下 → 衰减（钳制 0-2）', () => {
    const x = HEADER_W + DEFAULT_ZOOM * 1;
    const y0 = RULER_H + TRACK_H / 2;
    (engine as any).onPointerDown(mkPointer('pointerdown', { x, y: y0, altKey: true }));
    // 向上 120px → +1.0
    (engine as any).onPointerMove(mkPointer('pointermove', { x, y: y0 - 120 }));
    expect(AUDIO_CLIP.volume).toBeCloseTo(2, 5);
    // 向下拖回 240px → -2.0 → 钳到 0
    (engine as any).onPointerMove(mkPointer('pointermove', { x, y: y0 - 120 + 240 }));
    expect(AUDIO_CLIP.volume).toBeCloseTo(0, 5);
    // 松开
    (engine as any).onPointerUp(mkPointer('pointerup', { x, y: y0 + 200 }));
    expect((engine as any).drag.mode).toBe('none');
  });

  it('无 Alt 时不会进入 gain 模式', () => {
    const x = HEADER_W + DEFAULT_ZOOM * 1;
    const y = RULER_H + TRACK_H / 2;
    (engine as any).onPointerDown(mkPointer('pointerdown', { x, y }));
    expect((engine as any).drag.mode).not.toBe('gain');
  });
});
