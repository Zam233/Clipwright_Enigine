import { useEffect, useMemo } from 'react';
import { keybindingEngine } from './KeybindingEngine';
import { Keyboard, X } from 'lucide-react';

/**
 * ShortcutCheatSheet — an overlay listing every registered binding,
 * grouped by category, styled like a physical keycap reference.
 */
export function ShortcutCheatSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);
  const grouped = useMemo(() => {
    const map = new Map<string, { combo: string; label: string; isCustom: boolean }[]>();
    for (const b of keybindingEngine.list()) {
      const combo = keybindingEngine.effectiveCombo(b);
      if (!map.has(b.category)) map.set(b.category, []);
      map.get(b.category)!.push({ combo, label: b.label, isCustom: combo !== b.combo });
    }
    return [...map.entries()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-[2px]"
      onClick={onClose}>
      <div
        className="relative w-[560px] max-w-[92vw] max-h-[80vh] bg-surface-container-high border border-outline-variant/50
          rounded-cw-lg shadow-2xl shadow-black/60 overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-outline-variant/30 bg-surface-container">
          <span className="w-9 h-9 rounded-cw-sm bg-primary-container flex items-center justify-center">
            <Keyboard className="w-4.5 h-4.5 text-on-primary-container" />
          </span>
          <div className="flex-1">
            <h2 className="text-title-sm font-bold text-on-surface">快捷键速查表</h2>
            <p className="font-mono text-caption text-on-surface-variant">CLIPWRIGHT · KEYBOARD REFERENCE</p>
          </div>
          <span className="font-mono text-caption text-on-surface-variant border border-outline-variant/40 rounded-cw-xs px-1.5 py-0.5">Ctrl /</span>
          <button onClick={onClose} className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* bindings */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {grouped.map(([category, items]) => (
            <div key={category}>
              <p className="font-mono text-label-sm tracking-[0.2em] text-primary uppercase mb-2.5">{category}</p>
              <div className="space-y-1.5">
                {items.map((item) => (
                  <div key={item.combo} className="flex items-center justify-between px-3 py-2 rounded-cw-sm bg-surface-container
                    border border-outline-variant/20 hover:border-outline/50 transition-colors duration-short3">
                    <span className="text-body-sm text-on-surface">
                      {item.label}
                      {item.isCustom && <span className="ml-2 text-caption text-primary">自定义</span>}
                    </span>
                    <span className="flex items-center gap-1">
                      {item.combo.split('+').map((part, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <span className="text-caption text-on-surface-variant/50">+</span>}
                          <kbd className="inline-flex items-center justify-center min-w-[28px] h-[26px] px-2
                            bg-surface-container-high border border-outline-variant/50 border-b-2 rounded-cw-xs
                            font-mono text-label-sm text-on-surface shadow-sm capitalize">
                            {keyLabel(part)}
                          </kbd>
                        </span>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function keyLabel(part: string): string {
  const map: Record<string, string> = {
    ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', space: '␣',
    arrowleft: '←', arrowright: '→', arrowup: '↑', arrowdown: '↓',
    home: 'Home', end: 'End', delete: 'Del', escape: 'Esc', '/': '/',
  };
  return map[part] ?? part.toUpperCase();
}
