import { useToastStore } from '@/stores/toastStore';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';
import { cn } from '@/lib/utils';

const STYLES = {
  info: 'border-primary/40 bg-surface-container-high text-on-surface',
  success: 'border-track-video/50 bg-surface-container-high text-on-surface',
  error: 'border-error/50 bg-surface-container-high text-on-surface',
};

const ICONS = {
  info: Info,
  success: CheckCircle2,
  error: AlertCircle,
};

const ICON_COLORS = {
  info: 'text-primary',
  success: 'text-track-video',
  error: 'text-error',
};

/** Toaster — renders transient notifications (fixed bottom-right). Mount once in App. */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => {
        const Icon = ICONS[t.type];
        return (
          <div
            key={t.id}
            className={cn(
              'flex items-start gap-2.5 px-3.5 py-2.5 rounded-cw-md border shadow-lg shadow-black/30',
              'animate-[fadeIn_0.15s_ease-out]',
              STYLES[t.type],
            )}
          >
            <Icon className={cn('w-4 h-4 mt-0.5 shrink-0', ICON_COLORS[t.type])} />
            <span className="flex-1 text-body-sm leading-snug">{t.message}</span>
            <button
              onClick={() => dismiss(t.id)}
              className="p-0.5 text-on-surface-variant/50 hover:text-on-surface transition-colors cursor-pointer shrink-0"
              aria-label="关闭通知"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
