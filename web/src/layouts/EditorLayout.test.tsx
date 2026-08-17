// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { EditorLayout } from './EditorLayout';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    setPanelWidth: vi.fn(),
    setTimelineHeight: vi.fn(),
    panels: { assets: true, properties: true, agent: true },
    panelWidths: { assets: 260, properties: 320, agent: 300 },
    timelineHeight: 260,
  },
}));

vi.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: () => ({
    panels: mocks.panels,
    panelWidths: mocks.panelWidths,
    timelineHeight: mocks.timelineHeight,
    setPanelWidth: mocks.setPanelWidth,
    setTimelineHeight: mocks.setTimelineHeight,
  }),
}));

vi.mock('@/features/assets/AssetPanel', () => ({ AssetPanel: () => <div>assets</div> }));
vi.mock('@/features/preview/PreviewPanel', () => ({ PreviewPanel: () => <div>preview</div> }));
vi.mock('@/features/timeline/components/TimelinePanel', () => ({ TimelinePanel: () => <div>timeline</div> }));
vi.mock('@/features/properties/PropertiesPanel', () => ({ PropertiesPanel: () => <div>properties</div> }));
vi.mock('@/features/agent/AgentPanel', () => ({ AgentPanel: () => <div>agent</div> }));
vi.mock('@/features/timeline/components/EditorToolbar', () => ({ EditorToolbar: () => <div>toolbar</div> }));
vi.mock('@/features/agent/ReviewPanel', () => ({ ReviewPanel: () => <div>review</div> }));

vi.mock('@/stores/agentStore', () => ({
  useAgentStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ reviewMode: null, setReviewMode: vi.fn(), creativeBrief: null, productionPlan: null })
      : { reviewMode: null, setReviewMode: vi.fn(), creativeBrief: null, productionPlan: null },
}));

const storeMock = () => ({
  isSaving: false, lastSavedAt: null, saveError: null,
  currentTimeSec: 0, loopRegion: null, isLooping: false,
  toolMode: 'select',
  showFramesInRuler: false, setShowFramesInRuler: vi.fn(),
  undoStack: [], redoStack: [],
});
vi.mock('@/stores/projectStore', () => ({
  useProjectStore: (sel?: (s: unknown) => unknown) => sel ? sel(storeMock()) : storeMock(),
}));
vi.mock('@/stores/previewStore', () => ({
  usePreviewStore: (sel?: (s: unknown) => unknown) => sel ? sel(storeMock()) : storeMock(),
}));
vi.mock('@/stores/timelineStore', () => ({
  useTimelineStore: (sel?: (s: unknown) => unknown) => {
    const st = { ...storeMock(), timeline: { fps: 30, duration_sec: 0, tracks: [] } };
    return sel ? sel(st) : st;
  },
}));
vi.mock('@/stores/selectionStore', () => ({
  useSelectionStore: (sel?: (s: unknown) => unknown) => sel ? sel(storeMock()) : storeMock(),
}));
vi.mock('@/stores/historyStore', () => ({
  useHistoryStore: (sel?: (s: unknown) => unknown) => sel ? sel(storeMock()) : storeMock(),
}));
vi.mock('@/stores/settingsStore', () => ({
  useSettingsStore: (sel?: (s: unknown) => unknown) => sel ? sel(storeMock()) : storeMock(),
}));

afterEach(() => cleanup());

/** 触发分隔条拖拽：从 startX 拖到 endX（鼠标按下 → 移动 → 抬起）。 */
function dragDivider(divider: HTMLElement, startX: number, endX: number) {
  act(() => {
    fireEvent.mouseDown(divider, { clientX: startX, clientY: 100 });
    fireEvent.mouseMove(document, { clientX: endX, clientY: 100 });
    fireEvent.mouseUp(document);
  });
}

describe('EditorLayout 面板分隔条方向（BUG2 回归）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Agent 面板：向右拖 divider（dx>0）→ 面板收窄（w - dx）', () => {
    const { container } = render(<EditorLayout />);
    const dividers = container.querySelectorAll('.panel-divider');
    // 顺序：assets divider, properties divider, agent divider（timeline 是 panel-divider-h）
    expect(dividers.length).toBe(3);
    const agentDivider = dividers[2];
    dragDivider(agentDivider as HTMLElement, 500, 560); // dx = +60
    expect(mocks.setPanelWidth).toHaveBeenCalledWith('agent', 300 - 60); // 240，收窄
  });

  it('Agent 面板：向左拖 divider（dx<0）→ 面板加宽（w - dx）', () => {
    const { container } = render(<EditorLayout />);
    const dividers = container.querySelectorAll('.panel-divider');
    const agentDivider = dividers[2];
    dragDivider(agentDivider as HTMLElement, 500, 440); // dx = -60
    expect(mocks.setPanelWidth).toHaveBeenCalledWith('agent', 300 + 60); // 360，加宽
  });

  it('Assets 面板：向右拖 → 加宽（w + dx，divider 在右侧）', () => {
    const { container } = render(<EditorLayout />);
    const dividers = container.querySelectorAll('.panel-divider');
    dragDivider(dividers[0] as HTMLElement, 200, 260); // dx = +60
    expect(mocks.setPanelWidth).toHaveBeenCalledWith('assets', 260 + 60);
  });

  it('Properties 面板：向右拖 → 收窄（w - dx，divider 在左侧）', () => {
    const { container } = render(<EditorLayout />);
    const dividers = container.querySelectorAll('.panel-divider');
    dragDivider(dividers[1] as HTMLElement, 400, 460); // dx = +60
    expect(mocks.setPanelWidth).toHaveBeenCalledWith('properties', 320 - 60);
  });
});
