/**
 * keybindingStore — 快捷键自定义（C4）：bindingId → combo 覆盖，
 * localStorage 持久化（clipwright.keybindingOverrides）。
 */
import { create } from 'zustand';
import { loadPref, savePref } from '@/services/storage/localPrefs';

const STORAGE_KEY = 'keybindingOverrides';

/** 校验 combo 格式：小写字母/数字/常用功能键，可带 ctrl/shift/alt/mod 前缀。 */
export function isValidCombo(combo: string): boolean {
  if (!combo || combo.length > 48) return false;
  const parts = combo.toLowerCase().split('+').map((p) => p.trim());
  if (parts.some((p) => p === '')) return false;
  const modifiers = new Set(['ctrl', 'mod', 'meta', 'shift', 'alt']);
  const keys = parts.filter((p) => !modifiers.has(p));
  if (keys.length !== 1) return false;
  const key = keys[0];
  // 单字符（字母/数字/标点）或白名单功能键
  if (/^[a-z0-9]$/.test(key)) return true;
  if (/^[.,/;'\[\]\\`=\-]$/.test(key)) return true;
  const named = [
    'space', 'enter', 'tab', 'escape', 'delete', 'backspace', 'home', 'end',
    'pageup', 'pagedown', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright',
  ];
  if (named.includes(key)) return true;
  return /^f([1-9]|1[0-9]|2[0-4])$/.test(key);
}

interface KeybindingState {
  /** bindingId → 覆盖后的 combo */
  overrides: Record<string, string>;
  setCombo: (id: string, combo: string) => void;
  resetCombo: (id: string) => void;
  resetAll: () => void;
  getCombo: (id: string, fallback: string) => string;
}

function loadOverrides(): Record<string, string> {
  const raw = loadPref<Record<string, string>>(STORAGE_KEY, {});
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (typeof v === 'string' && isValidCombo(v)) out[k] = v;
  }
  return out;
}

export const useKeybindingStore = create<KeybindingState>((set, get) => ({
  overrides: loadOverrides(),

  setCombo: (id, combo) => {
    if (!isValidCombo(combo)) return;
    set((state) => {
      const overrides = { ...state.overrides, [id]: combo };
      savePref(STORAGE_KEY, overrides);
      return { overrides };
    });
  },

  resetCombo: (id) => {
    set((state) => {
      const overrides = { ...state.overrides };
      delete overrides[id];
      savePref(STORAGE_KEY, overrides);
      return { overrides };
    });
  },

  resetAll: () => {
    set({ overrides: {} });
    savePref(STORAGE_KEY, {});
  },

  getCombo: (id, fallback) => {
    const v = get().overrides[id];
    return v && isValidCombo(v) ? v : fallback;
  },
}));
