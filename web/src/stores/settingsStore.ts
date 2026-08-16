import { create } from 'zustand';
import { loadPref, savePref } from '@/services/storage/localPrefs';

interface SettingsState {
  apiBaseUrl: string;
  wsUrl: string;
  theme: 'dark' | 'light';
  language: 'zh' | 'en';
  authToken: string | null;
  autoSave: boolean;
  autoSaveIntervalSec: number;
  maxUndoHistory: number;
  snapEnabled: boolean;
  snapThresholdPx: number;
  snapToGrid: boolean;
  snapGridSec: number;
  cheatSheetOpen: boolean;
  showFramesInRuler: boolean;
  defaultFps: number;
  defaultResolution: { width: number; height: number };

  // Actions
  setApiBaseUrl: (url: string) => void;
  setWsUrl: (url: string) => void;
  setTheme: (theme: 'dark' | 'light') => void;
  setLanguage: (lang: 'zh' | 'en') => void;
  setAuthToken: (token: string | null) => void;
  setAutoSave: (enabled: boolean) => void;
  setSnapEnabled: (enabled: boolean) => void;
  setSnapThreshold: (px: number) => void;
  setSnapToGrid: (enabled: boolean) => void;
  setSnapGridSec: (sec: number) => void;
  setCheatSheetOpen: (open: boolean) => void;
  setShowFramesInRuler: (v: boolean) => void;
  setDefaultFps: (fps: number) => void;
  setDefaultResolution: (res: { width: number; height: number }) => void;
}

/** Load persisted editor preferences. */
function loadEditorPrefs() {
  const raw = loadPref<Record<string, unknown>>('editorPrefs', {});
  const num = (v: unknown, def: number, lo: number, hi: number) =>
    typeof v === 'number' && isFinite(v) ? Math.min(Math.max(v, lo), hi) : def;
  return {
    snapEnabled: typeof raw.snapEnabled === 'boolean' ? raw.snapEnabled : true,
    snapThresholdPx: num(raw.snapThresholdPx, 8, 0, 100),
    snapToGrid: typeof raw.snapToGrid === 'boolean' ? raw.snapToGrid : false,
    snapGridSec: num(raw.snapGridSec, 1, 0.05, 60),
    showFramesInRuler: typeof raw.showFramesInRuler === 'boolean' ? raw.showFramesInRuler : false,
    theme: raw.theme === 'light' ? ('light' as const) : ('dark' as const),
  };
}

/** 加载持久化的连接配置（API 地址 / WS 地址 / 鉴权 Token）。 */
function loadConnectionPrefs() {
  const raw = loadPref<Record<string, unknown>>('connectionPrefs', {});
  return {
    apiBaseUrl: typeof raw.apiBaseUrl === 'string' && raw.apiBaseUrl !== '' ? raw.apiBaseUrl : undefined,
    wsUrl: typeof raw.wsUrl === 'string' && raw.wsUrl !== '' ? raw.wsUrl : undefined,
    authToken: typeof raw.authToken === 'string' ? raw.authToken : null,
  };
}

const prefs = loadEditorPrefs();
const connPrefs = loadConnectionPrefs();

export const useSettingsStore = create<SettingsState>((set) => ({
  apiBaseUrl: connPrefs.apiBaseUrl || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  wsUrl: connPrefs.wsUrl || import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws',
  theme: prefs.theme,
  language: 'zh',
  authToken: connPrefs.authToken,
  autoSave: true,
  autoSaveIntervalSec: 30,
  maxUndoHistory: 200,
  snapEnabled: prefs.snapEnabled,
  snapThresholdPx: prefs.snapThresholdPx,
  snapToGrid: prefs.snapToGrid,
  snapGridSec: prefs.snapGridSec,
  cheatSheetOpen: false,
  showFramesInRuler: prefs.showFramesInRuler,
  defaultFps: 30,
  defaultResolution: { width: 1920, height: 1080 },

  setApiBaseUrl: (url) => { set({ apiBaseUrl: url }); persistConnectionPrefs(); },
  setWsUrl: (url) => { set({ wsUrl: url }); persistConnectionPrefs(); },
  setTheme: (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.classList.toggle('light', theme === 'light');
    set({ theme });
    persistEditorPrefs();
  },
  setLanguage: (lang) => set({ language: lang }),
  setAuthToken: (token) => { set({ authToken: token }); persistConnectionPrefs(); },
  setAutoSave: (enabled) => set({ autoSave: enabled }),
  setSnapEnabled: (enabled) => { set({ snapEnabled: enabled }); persistEditorPrefs(); },
  setSnapThreshold: (px) => { set({ snapThresholdPx: px }); persistEditorPrefs(); },
  setSnapToGrid: (enabled) => { set({ snapToGrid: enabled }); persistEditorPrefs(); },
  setSnapGridSec: (sec) => { set({ snapGridSec: sec }); persistEditorPrefs(); },
  setCheatSheetOpen: (open) => set({ cheatSheetOpen: open }),
  setShowFramesInRuler: (v) => { set({ showFramesInRuler: v }); persistEditorPrefs(); },
  setDefaultFps: (fps) => set({ defaultFps: fps }),
  setDefaultResolution: (res) => set({ defaultResolution: res }),
}));

function persistEditorPrefs() {
  const s = useSettingsStore.getState();
  savePref('editorPrefs', {
    snapEnabled: s.snapEnabled,
    snapThresholdPx: s.snapThresholdPx,
    snapToGrid: s.snapToGrid,
    snapGridSec: s.snapGridSec,
    showFramesInRuler: s.showFramesInRuler,
    theme: s.theme,
  });
}

/** 持久化连接配置到 localStorage（键：clipwright.connectionPrefs）。 */
function persistConnectionPrefs() {
  const s = useSettingsStore.getState();
  savePref('connectionPrefs', {
    apiBaseUrl: s.apiBaseUrl,
    wsUrl: s.wsUrl,
    authToken: s.authToken,
  });
}

// Apply stored theme on module load
document.documentElement.classList.toggle('dark', prefs.theme === 'dark');
document.documentElement.classList.toggle('light', prefs.theme === 'light');
