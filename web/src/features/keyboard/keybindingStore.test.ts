// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useKeybindingStore, isValidCombo } from './keybindingStore';
import { keybindingEngine } from './KeybindingEngine';

describe('keybindingStore (C4 快捷键自定义)', () => {
  beforeEach(() => {
    localStorage.clear();
    useKeybindingStore.setState({ overrides: {} });
  });

  it('isValidCombo 校验', () => {
    expect(isValidCombo('ctrl+z')).toBe(true);
    expect(isValidCombo('shift+arrowup')).toBe(true);
    expect(isValidCombo('alt+f5')).toBe(true);
    expect(isValidCombo('space')).toBe(true);
    expect(isValidCombo('mod+s')).toBe(true);
    expect(isValidCombo('ctrl+shift+delete')).toBe(true);
    expect(isValidCombo('')).toBe(false);
    expect(isValidCombo('ctrl+')).toBe(false);
    expect(isValidCombo('ctrl+shift+z+extra')).toBe(false);
    expect(isValidCombo('不合法键')).toBe(false);
  });

  it('setCombo / resetCombo / resetAll / getCombo 行为', () => {
    const s = useKeybindingStore.getState();
    s.setCombo('timeline-add-marker', 'ctrl+k');
    expect(useKeybindingStore.getState().getCombo('timeline-add-marker', 'm')).toBe('ctrl+k');
    // 非法 combo 被忽略
    s.setCombo('timeline-add-marker', '不合法');
    expect(useKeybindingStore.getState().getCombo('timeline-add-marker', 'm')).toBe('ctrl+k');
    // 未覆盖 → fallback
    expect(useKeybindingStore.getState().getCombo('nope', 'space')).toBe('space');
    // reset 单个
    s.resetCombo('timeline-add-marker');
    expect(useKeybindingStore.getState().getCombo('timeline-add-marker', 'm')).toBe('m');
  });

  it('overrides 持久化到 localStorage', () => {
    useKeybindingStore.getState().setCombo('timeline-zoom-in', 'ctrl+=');
    const raw = JSON.parse(localStorage.getItem('clipwright.keybindingOverrides')!);
    expect(raw['timeline-zoom-in']).toBe('ctrl+=');
  });
});

describe('KeybindingEngine effectiveCombo (C4)', () => {
  beforeEach(() => {
    localStorage.clear();
    useKeybindingStore.setState({ overrides: {} });
  });

  it('未覆盖时返回注册默认 combo', () => {
    const unsub = keybindingEngine.register({
      id: 'test-bind', combo: 'shift+m', label: '跳转', category: '测试',
      handler: vi.fn(),
    });
    expect(keybindingEngine.effectiveCombo(keybindingEngine.list().find((b) => b.id === 'test-bind')!)).toBe('shift+m');
    unsub();
  });

  it('覆盖后 effectiveCombo 返回新 combo', () => {
    const unsub = keybindingEngine.register({
      id: 'test-bind2', combo: 'shift+m', label: '跳转', category: '测试',
      handler: vi.fn(),
    });
    useKeybindingStore.getState().setCombo('test-bind2', 'ctrl+j');
    const b = keybindingEngine.list().find((x) => x.id === 'test-bind2')!;
    expect(keybindingEngine.effectiveCombo(b)).toBe('ctrl+j');
    unsub();
  });
});
