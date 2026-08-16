// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { DubView } from './DubView';
import { useProjectStore } from '@/stores/projectStore';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    voiceList: vi.fn(),
    voiceDub: vi.fn(),
    getAudioUrl: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({
  voiceApi: {
    list: mocks.voiceList,
    dub: mocks.voiceDub,
    getAudioUrl: mocks.getAudioUrl,
  },
}));

vi.mock('@/pages/VoicePage', () => ({ VoicePage: () => null }));
vi.mock('@/components/shared/AudioPlayer', () => ({ AudioPlayer: () => null }));
vi.mock('@/stores/toastStore', () => ({ toast: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.voiceList.mockResolvedValue([]);
  mocks.getAudioUrl.mockReturnValue('http://localhost:8000/audio/x.mp3');
  useProjectStore.setState({
    voiceId: 'v1',
    scriptText: '第一段。第二段。',
    dubSegments: null,
  });
});

afterEach(() => cleanup());

describe('B21: dub segments wired into projectStore', () => {
  it('配音完成后 projectStore.dubSegments 非空且含 start/end/text', async () => {
    mocks.voiceDub.mockResolvedValue({
      segments: [
        { index: 0, text: '第一段', duration_sec: 3 },
        { index: 1, text: '第二段', duration_sec: 4 },
      ],
      total: 2,
      total_duration_sec: 7,
    });

    render(<DubView />);
    // 确认配音
    fireEvent.click(screen.getByText('确认配音'));
    await waitFor(() => expect(mocks.voiceDub).toHaveBeenCalled());

    const segments = useProjectStore.getState().dubSegments;
    expect(segments).not.toBeNull();
    expect(segments!.length).toBe(2);
    expect(segments![0]).toEqual({ start: 0, end: 3, text: '第一段' });
    expect(segments![1]).toEqual({ start: 3, end: 7, text: '第二段' });
  });

  it('配音失败不写 dubSegments（保持 null）', async () => {
    mocks.voiceDub.mockRejectedValue(new Error('dub failed'));

    render(<DubView />);
    fireEvent.click(screen.getByText('确认配音'));
    await waitFor(() => expect(mocks.voiceDub).toHaveBeenCalled());

    expect(useProjectStore.getState().dubSegments).toBeNull();
  });
});
