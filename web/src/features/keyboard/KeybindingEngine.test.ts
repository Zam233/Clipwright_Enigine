// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { KeybindingEngine } from './KeybindingEngine';

function fireKeyDown(engine: KeybindingEngine, tag: string, key: string, init: KeyboardEventInit = {}): void {
  const el = document.createElement(tag);
  document.body.appendChild(el);
  engine.attach();
  el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
  engine.detach();
  document.body.removeChild(el);
}

function registerShortcut(engine: KeybindingEngine, combo: string): ReturnType<typeof vi.fn> {
  const handler = vi.fn();
  engine.register({ id: `test-${combo}`, combo, label: 'test', category: 'test', handler });
  return handler;
}

describe('KeybindingEngine target guard', () => {
  it("blocks single-key 's' when a BUTTON is focused", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 's');
    fireKeyDown(engine, 'BUTTON', 's');
    expect(handler).not.toHaveBeenCalled();
  });

  it("blocks single-key 's' when an anchor (A) is focused", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 's');
    fireKeyDown(engine, 'A', 's');
    expect(handler).not.toHaveBeenCalled();
  });

  it('still fires modifier shortcuts (ctrl+s) when a BUTTON is focused', () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 'ctrl+s');
    fireKeyDown(engine, 'BUTTON', 's', { ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("blocks single-key 's' when an INPUT is focused (unchanged)", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 's');
    fireKeyDown(engine, 'INPUT', 's');
    expect(handler).not.toHaveBeenCalled();
  });

  it("blocks single-key 's' when a TEXTAREA is focused (unchanged)", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 's');
    fireKeyDown(engine, 'TEXTAREA', 's');
    expect(handler).not.toHaveBeenCalled();
  });

  it("fires single-key 's' on a plain DIV target", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 's');
    fireKeyDown(engine, 'DIV', 's');
    expect(handler).toHaveBeenCalledTimes(1);
  });
});

function fireKeyDownEvent(engine: KeybindingEngine, tag: string, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const el = document.createElement(tag);
  document.body.appendChild(el);
  engine.attach();
  const evt = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  el.dispatchEvent(evt);
  engine.detach();
  document.body.removeChild(el);
  return evt;
}

describe('KeybindingEngine browser-reserved combos', () => {
  it('ctrl+e fires handler and prevents default (blocks address-bar focus)', () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, 'ctrl+e');
    const evt = fireKeyDownEvent(engine, 'DIV', 'e', { ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBe(true);
  });

  it("'/' (no modifier) fires handler and prevents default", () => {
    const engine = new KeybindingEngine();
    const handler = registerShortcut(engine, '/');
    const evt = fireKeyDownEvent(engine, 'DIV', '/');
    expect(handler).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBe(true);
  });
});
