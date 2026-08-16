// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PropertiesPanel } from './PropertiesPanel';
import { useTimelineStore } from '@/stores/timelineStore';
import { useSelectionStore } from '@/stores/selectionStore';
import { createEmptyTimeline, createDefaultClip } from '@/types/timeline';

vi.mock('@/services/api', () => ({
  animationApi: {
    list: vi.fn().mockResolvedValue([]),
    onscreen: vi.fn().mockResolvedValue([]),
    transitions: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('@/services/media/mediaManager', () => ({
  mediaManager: {
    getCachedWaveform: vi.fn(() => null),
    ensureWaveform: vi.fn(),
    get: vi.fn(() => null),
    registerTimeline: vi.fn(),
  },
}));

describe('PropertiesPanel M6 (音频增益 + 淡入淡出 UI)', () => {
  beforeEach(() => {
    const tl = createEmptyTimeline('t');
    const tid = 'audio1';
    tl.tracks = [{ id: tid, name: 'A1', kind: 'audio', index: 0, locked: false, muted: false, clips: [
      createDefaultClip({ id: 'c1', kind: 'audio', track_id: tid, duration_sec: 5, volume: 1 }),
    ] }];
    useTimelineStore.setState({ timeline: tl, isDirty: false });
    useSelectionStore.setState({ selectedClipIds: ['c1'], selectedTrackId: tid });
  });

  it('音频片段显示 增益(百分比标签)、淡入/淡出滑块', () => {
    render(<PropertiesPanel />);
    expect(screen.getByText(/增益 100%/)).toBeTruthy();
    expect(screen.getByText('淡入 (s)')).toBeTruthy();
    expect(screen.getByText('淡出 (s)')).toBeTruthy();
  });

  it('非音频片段不显示淡入/淡出', () => {
    const tl = useTimelineStore.getState().timeline;
    tl.tracks = [{ id: 'v1', name: 'V1', kind: 'video', index: 0, locked: false, muted: false, clips: [
      createDefaultClip({ id: 'c2', kind: 'video', track_id: 'v1', duration_sec: 5 }),
    ] }];
    useTimelineStore.setState({ timeline: tl });
    useSelectionStore.setState({ selectedClipIds: ['c2'], selectedTrackId: 'v1' });
    render(<PropertiesPanel />);
    expect(screen.queryByText('淡入 (s)')).toBeNull();
    expect(screen.queryByText('淡出 (s)')).toBeNull();
  });
});
