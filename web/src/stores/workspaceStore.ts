import { create } from 'zustand';
import { loadPref, savePref } from '@/services/storage/localPrefs';

interface WorkspacePanel {
  assets: boolean;
  properties: boolean;
  agent: boolean;
}

interface WorkspaceState {
  /** Panel visibility */
  panels: WorkspacePanel;
  /** Panel widths in pixels */
  panelWidths: {
    assets: number;
    properties: number;
    agent: number;
  };
  /** Timeline height in pixels */
  timelineHeight: number;
  /** Whether the timeline is collapsed */
  timelineCollapsed: boolean;
  /** Active bottom tab in timeline area */
  activeBottomTab: 'timeline' | 'keyframes' | 'audio';

  // Actions
  togglePanel: (panel: keyof WorkspacePanel) => void;
  setPanelWidth: (panel: keyof WorkspacePanel, width: number) => void;
  setTimelineHeight: (height: number) => void;
  toggleTimelineCollapsed: () => void;
  setActiveBottomTab: (tab: 'timeline' | 'keyframes' | 'audio') => void;
  resetLayout: () => void;
}

const DEFAULT_PANEL_WIDTHS = {
  assets: 280,
  properties: 300,
  agent: 320,
};

const DEFAULT_TIMELINE_HEIGHT = 280;

/** Safe layout loader — guards against malformed localStorage data. */
function loadLayout() {
  try {
    const raw = loadPref('layout', {
      panels: { assets: true, properties: true, agent: true },
      panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      timelineHeight: DEFAULT_TIMELINE_HEIGHT,
    });
    return {
      panels: raw?.panels && typeof raw.panels === 'object'
        ? {
          // 缺失的键视为默认可见（旧版本数据没有 agent 键时不应把面板永久隐藏）
          assets: raw.panels.assets === undefined ? true : Boolean(raw.panels.assets),
          properties: raw.panels.properties === undefined ? true : Boolean(raw.panels.properties),
          agent: raw.panels.agent === undefined ? true : Boolean(raw.panels.agent),
        }
        : { assets: true, properties: true, agent: true },
      panelWidths: raw?.panelWidths && typeof raw.panelWidths === 'object'
        ? {
          assets: typeof raw.panelWidths.assets === 'number' ? raw.panelWidths.assets : DEFAULT_PANEL_WIDTHS.assets,
          properties: typeof raw.panelWidths.properties === 'number' ? raw.panelWidths.properties : DEFAULT_PANEL_WIDTHS.properties,
          agent: typeof raw.panelWidths.agent === 'number' ? raw.panelWidths.agent : DEFAULT_PANEL_WIDTHS.agent,
        }
        : { ...DEFAULT_PANEL_WIDTHS },
      timelineHeight: typeof raw?.timelineHeight === 'number' ? raw.timelineHeight : DEFAULT_TIMELINE_HEIGHT,
    };
  } catch {
    return {
      panels: { assets: true, properties: true, agent: true },
      panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      timelineHeight: DEFAULT_TIMELINE_HEIGHT,
    };
  }
}

const layout = loadLayout();

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  panels: layout.panels,
  panelWidths: layout.panelWidths,
  timelineHeight: layout.timelineHeight,
  timelineCollapsed: false,
  activeBottomTab: 'timeline',

  togglePanel: (panel) =>
    set((state) => ({
      panels: { ...state.panels, [panel]: !state.panels[panel] },
    })),

  setPanelWidth: (panel, width) =>
    set((state) => ({
      panelWidths: { ...state.panelWidths, [panel]: Math.max(200, Math.min(500, width)) },
    })),

  setTimelineHeight: (height) =>
    set({ timelineHeight: Math.max(150, Math.min(600, height)) }),

  toggleTimelineCollapsed: () =>
    set((state) => ({ timelineCollapsed: !state.timelineCollapsed })),

  setActiveBottomTab: (tab) => set({ activeBottomTab: tab }),

  resetLayout: () =>
    set({
      panels: { assets: true, properties: true, agent: true },
      panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      timelineHeight: DEFAULT_TIMELINE_HEIGHT,
      timelineCollapsed: false,
    }),
}));

// Persist layout changes (debounced to avoid excessive localStorage writes)
let _persistTimer: ReturnType<typeof setTimeout> | null = null;
useWorkspaceStore.subscribe((state) => {
  if (_persistTimer) clearTimeout(_persistTimer);
  _persistTimer = setTimeout(() => {
    savePref('layout', {
      panels: state.panels,
      panelWidths: state.panelWidths,
      timelineHeight: state.timelineHeight,
    });
  }, 300);
});
