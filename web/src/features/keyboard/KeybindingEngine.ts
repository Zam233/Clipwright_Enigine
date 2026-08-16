/**
 * KeybindingEngine — a centralized shortcut system.
 *
 * Bindings declare a combo string ("ctrl+z", "space", "shift+delete", "s"),
 * a human-readable label (for the cheat sheet), and an optional `when`
 * guard. The engine normalizes keydown events and dispatches to handlers.
 *
 * C4: 支持快捷键自定义 — 匹配时通过 keybindingStore 解析「生效 combo」
 * （用户覆盖优先，否则用注册时的默认 combo）。
 */
import { useKeybindingStore } from './keybindingStore';

export interface KeyBinding {
  id: string;
  /** Combo like "ctrl+z", "space", "shift+delete", "arrowleft" */
  combo: string;
  label: string;
  category: string;
  handler: () => void;
  /** Optional guard — binding only fires when this returns true */
  when?: () => boolean;
}

/** Parse a combo string into a normalized matcher. */
function parseCombo(combo: string) {
  const parts = combo.toLowerCase().split('+').map((p) => p.trim());
  return {
    ctrl: parts.includes('ctrl') || parts.includes('mod') || parts.includes('meta'),
    shift: parts.includes('shift'),
    alt: parts.includes('alt'),
    key: parts.filter((p) => !['ctrl', 'mod', 'meta', 'shift', 'alt'].includes(p))[0] ?? '',
  };
}

/** Normalize a KeyboardEvent key name to match combo keys. */
function normalizeKey(e: KeyboardEvent): string {
  const k = e.key.toLowerCase();
  const map: Record<string, string> = {
    ' ': 'space', spacebar: 'space',
    esc: 'escape',
    arrowup: 'arrowup', arrowdown: 'arrowdown', arrowleft: 'arrowleft', arrowright: 'arrowright',
  };
  return map[k] ?? k;
}

function isTypingTarget(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

/** Interactive controls (buttons/links) — single-key shortcuts should not fire while focused. */
function isInteractiveControl(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement;
  const tag = el.tagName;
  return tag === 'BUTTON' || tag === 'A';
}

export class KeybindingEngine {
  private bindings: KeyBinding[] = [];
  private attached = false;

  register(binding: KeyBinding): () => void {
    this.bindings.push(binding);
    return () => this.unregister(binding.id);
  }

  registerMany(bindings: KeyBinding[]): () => void {
    bindings.forEach((b) => this.bindings.push(b));
    return () => bindings.forEach((b) => this.unregister(b.id));
  }

  unregister(id: string): void {
    this.bindings = this.bindings.filter((b) => b.id !== id);
  }

  /** 生效 combo：用户覆盖（C4）优先，否则注册默认值。 */
  effectiveCombo(binding: KeyBinding): string {
    return useKeybindingStore.getState().getCombo(binding.id, binding.combo);
  }

  /** All registered bindings (for the cheat sheet). */
  list(): KeyBinding[] {
    return [...this.bindings];
  }

  private onKeyDown = (e: KeyboardEvent) => {
    // Don't hijack typing in form fields (unless combo uses a modifier)
    const combo = this.match(e);
    if (!combo) return;
    const hasModifier = e.ctrlKey || e.metaKey || e.altKey;
    if (isTypingTarget(e) && !hasModifier) return;
    // In text inputs, let native browser shortcuts (cut/copy/paste/undo/redo/save) pass through
    if (isTypingTarget(e) && hasModifier) {
      const rawKey = normalizeKey(e);
      if ((e.ctrlKey || e.metaKey) && ['z', 'c', 'v', 'x', 'a', 's'].includes(rawKey)) return;
    }
    // Don't fire single-key shortcuts while a button/link is focused (Space would re-click it),
    // but keep modifier combos (Ctrl/Cmd/Alt) working — e.g. Ctrl+S with a button focused.
    if (isInteractiveControl(e) && !hasModifier) return;

    const binding = this.bindings.find((b) => {
      const m = parseCombo(this.effectiveCombo(b));
      if (m.key !== combo.key) return false;
      if (m.ctrl !== (e.ctrlKey || e.metaKey)) return false;
      if (m.shift !== e.shiftKey) return false;
      if (m.alt !== e.altKey) return false;
      if (b.when && !b.when()) return false;
      return true;
    });

    if (binding) {
      // 长按不重复触发（避免 Space 反复播放/暂停、s 重复切分、历史栈被刷屏）
      if (e.repeat) return;
      e.preventDefault();
      e.stopPropagation();
      binding.handler();
    }
  };

  private match(e: KeyboardEvent): { key: string } | null {
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return null;
    return { key: normalizeKey(e) };
  }

  attach(): void {
    if (this.attached) return;
    window.addEventListener('keydown', this.onKeyDown, true);
    this.attached = true;
  }

  detach(): void {
    window.removeEventListener('keydown', this.onKeyDown, true);
    this.attached = false;
  }
}

/** Singleton engine for the app. */
export const keybindingEngine = new KeybindingEngine();
