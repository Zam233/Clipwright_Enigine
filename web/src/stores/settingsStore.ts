import { create } from 'zustand';
import { loadPref, savePref } from '@/services/storage/localPrefs';

interface SettingsState {
  apiBaseUrl: string;
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

/** 加载持久化的连接配置（API 地址 / 鉴权 Token）。WS 已移除（W9）。 */
function loadConnectionPrefs() {
  const raw = loadPref<Record<string, unknown>>('connectionPrefs', {});
  return {
    apiBaseUrl: typeof raw.apiBaseUrl === 'string' && raw.apiBaseUrl !== '' ? raw.apiBaseUrl : undefined,
    authToken: typeof raw.authToken === 'string' ? raw.authToken : null,
  };
}

const prefs = loadEditorPrefs();
const connPrefs = loadConnectionPrefs();

export const useSettingsStore = create<SettingsState>((set) => ({
  // P0-11: 默认空串（同源）；持久化的旧 localStorage 值仍会被 loadConnectionPrefs 采用——
  // 仅当用户显式在设置中清空 API 地址时回落到空串。WS 已移除（W9）。
  apiBaseUrl: connPrefs.apiBaseUrl || import.meta.env.VITE_API_BASE_URL || '',
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
    authToken: s.authToken,
  });
}

// Apply stored theme on module load
document.documentElement.classList.toggle('dark', prefs.theme === 'dark');
document.documentElement.classList.toggle('light', prefs.theme === 'light');
