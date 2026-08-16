import { create } from 'zustand';

export type ToastType = 'info' | 'success' | 'error';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastState {
  toasts: Toast[];
  show: (message: string, type?: ToastType) => void;
  dismiss: (id: number) => void;
}

let _nextId = 1;
// 跟踪定时器：手动 dismiss 后取消待执行的自动关闭，避免无界定时器堆积
const _timers = new Map<number, ReturnType<typeof setTimeout>>();

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  show: (message, type = 'info') => {
    const id = _nextId++;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { id, message, type }] }));
    // Auto-dismiss after 4s
    const timer = setTimeout(() => get().dismiss(id), 4000);
    _timers.set(id, timer);
  },
  dismiss: (id) => {
    const timer = _timers.get(id);
    if (timer) { clearTimeout(timer); _timers.delete(id); }
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
}));

/** Convenience helper for use outside React components. */
export function toast(message: string, type?: ToastType) {
  useToastStore.getState().show(message, type);
}
