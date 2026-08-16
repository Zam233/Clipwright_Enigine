// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { extractImportError, EditorToolbar } from './EditorToolbar';

const { mocks, stores } = vi.hoisted(() => {
  const stores = {
    project: {
      projectName: '\u6d4b\u8bd5\u9879\u76ee',
      isSaving: false,
      lastSavedAt: null as string | null,
      saveError: null as string | null,
      projectId: 'proj_1',
      setProjectName: vi.fn(),
      requestSave: vi.fn(),
    },
    preview: {
      isPlaying: false,
      currentTimeSec: 10,
      durationSec: 60,
      fps: 30,
      togglePlay: vi.fn(),
      stepForward: vi.fn(),
      stepBackward: vi.fn(),
      seekToStart: vi.fn(),
      seekToEnd: vi.fn(),
    },
    workspace: {
      panels: { assets: true, agent: true, properties: true },
      togglePanel: vi.fn(),
    },
    history: {
      undoStack: [] as unknown[],
      redoStack: [] as unknown[],
      undo: vi.fn(),
      redo: vi.fn(),
      pushState: vi.fn(),
    },
    timeline: {
      timeline: { tracks: [] },
      addTrack: vi.fn(),
      addClip: vi.fn(),
      setTimeline: vi.fn(),
    },
    selection: { selectedClipIds: [] },
    settings: { setCheatSheetOpen: vi.fn() },
  };
  return {
    mocks: { toast: vi.fn(), post: vi.fn(), navigate: vi.fn() },
    stores,
  };
});

vi.mock('@/stores/toastStore', () => ({ toast: mocks.toast }));
vi.mock('@/services/api', () => ({ getApiClient: () => ({ post: mocks.post }) }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));

vi.mock('@/stores/projectStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useProjectStore: any = (sel: (s: unknown) => unknown) => sel(stores.project);
  useProjectStore.getState = () => stores.project;
  return { useProjectStore };
});

vi.mock('@/stores/previewStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const usePreviewStore: any = (sel: (s: unknown) => unknown) => sel(stores.preview);
  usePreviewStore.getState = () => stores.preview;
  return { usePreviewStore };
});

vi.mock('@/stores/workspaceStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useWorkspaceStore: any = (sel: (s: unknown) => unknown) => sel(stores.workspace);
  useWorkspaceStore.getState = () => stores.workspace;
  return { useWorkspaceStore };
});

vi.mock('@/stores/historyStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useHistoryStore: any = (sel: (s: unknown) => unknown) => sel(stores.history);
  useHistoryStore.getState = () => stores.history;
  return { useHistoryStore };
});

vi.mock('@/stores/timelineStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useTimelineStore: any = (sel: (s: unknown) => unknown) => sel(stores.timeline);
  useTimelineStore.getState = () => stores.timeline;
  return { useTimelineStore };
});

vi.mock('@/stores/selectionStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useSelectionStore: any = (sel: (s: unknown) => unknown) => sel(stores.selection);
  useSelectionStore.getState = () => stores.selection;
  return { useSelectionStore };
});

vi.mock('@/stores/settingsStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useSettingsStore: any = (sel: (s: unknown) => unknown) => sel(stores.settings);
  useSettingsStore.getState = () => stores.settings;
  return { useSettingsStore };
});

afterEach(() => cleanup());

describe('extractImportError (U6)', () => {
  it('maps 400 to the format-error message', () => {
    const msg = extractImportError({ response: { status: 400 } }, 'EDL \u5bfc\u5165\u5931\u8d25');
    expect(msg).toContain('\u683c\u5f0f\u9519\u8bef');
    expect(msg).toContain('EDL \u5bfc\u5165\u5931\u8d25');
  });

  it('maps 422 to the validation-failure message', () => {
    const msg = extractImportError({ response: { status: 422 } }, 'EDL \u5bfc\u5165\u5931\u8d25');
    expect(msg).toContain('\u6570\u636e\u6821\u9a8c\u5931\u8d25');
  });

  it('maps 500 to the backend-unreachable message', () => {
    const msg = extractImportError({ response: { status: 500 } }, 'EDL \u5bfc\u5165\u5931\u8d25');
    expect(msg).toContain('\u540e\u7aef\u4e0d\u53ef\u8fbe');
  });

  it('maps network errors (no response) to the backend-unreachable message', () => {
    const msg = extractImportError(new Error('Network Error'), 'EDL \u5bfc\u5165\u5931\u8d25');
    expect(msg).toContain('\u540e\u7aef\u4e0d\u53ef\u8fbe');
  });

  it('produces DIFFERENT messages for 400 vs network errors', () => {
    const formatMsg = extractImportError({ response: { status: 400 } }, '\u97f3\u9891\u8f6c\u5f55\u5931\u8d25');
    const networkMsg = extractImportError(new Error('Network Error'), '\u97f3\u9891\u8f6c\u5f55\u5931\u8d25');
    expect(formatMsg).not.toBe(networkMsg);
    expect(formatMsg).toContain('\u683c\u5f0f\u9519\u8bef');
    expect(networkMsg).toContain('\u540e\u7aef\u4e0d\u53ef\u8fbe');
  });
});

describe('U6 toast wiring: audio transcribe failures', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('toasts the format-error message on 400 and the backend-unreachable message on network errors', async () => {
    const file = new File(['audio'], 'voice.mp3', { type: 'audio/mpeg' });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const fakeInput: any = { type: '', accept: '', files: [file] };
    fakeInput.click = () => { fakeInput.onchange?.(); };

    const realCreateElement = document.createElement.bind(document);
    const createSpy = vi.spyOn(document, 'createElement').mockImplementation(
      (tagName: string, opts?: ElementCreationOptions) =>
        tagName === 'input' ? (fakeInput as unknown as HTMLElement) : realCreateElement(tagName, opts),
    );

    try {
      mocks.post.mockRejectedValueOnce({ response: { status: 400 } });
      const { container } = render(<EditorToolbar />);
      const micBtn = container.querySelector('button .lucide-mic')?.closest('button') as HTMLButtonElement;
      expect(micBtn).toBeTruthy();

      fireEvent.click(micBtn);
      await waitFor(() => {
        expect(mocks.toast).toHaveBeenCalledWith(
          '\u97f3\u9891\u8f6c\u5f55\u5931\u8d25 \u2014 \u683c\u5f0f\u9519\u8bef\uff1a\u8bf7\u68c0\u67e5\u6587\u4ef6\u5185\u5bb9\u540e\u91cd\u8bd5',
          'error',
        );
      });

      mocks.post.mockRejectedValueOnce(new Error('Network Error'));
      fireEvent.click(micBtn);
      await waitFor(() => {
        expect(mocks.toast).toHaveBeenCalledWith('\u97f3\u9891\u8f6c\u5f55\u5931\u8d25 \u2014 \u540e\u7aef\u4e0d\u53ef\u8fbe', 'error');
      });

      const messages = mocks.toast.mock.calls.map((c) => c[0] as string);
      expect(messages).toHaveLength(2);
      expect(messages[0]).not.toBe(messages[1]);
    } finally {
      createSpy.mockRestore();
    }
  });
});