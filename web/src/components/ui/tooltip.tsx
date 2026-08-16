import * as React from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

interface TooltipProps {
  content: string;
  side?: 'top' | 'bottom' | 'left' | 'right';
  children: React.ReactNode;
}

function Tooltip({ content, side = 'top', children }: TooltipProps) {
  const triggerRef = React.useRef<HTMLDivElement>(null);
  const [pos, setPos] = React.useState<{ x: number; y: number } | null>(null);
  const tooltipId = React.useMemo(() => `tooltip-${Math.random().toString(36).slice(2, 8)}`, []);

  const show = () => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = side === 'top' ? rect.top : side === 'bottom' ? rect.bottom : rect.top + rect.height / 2;
    setPos({ x, y });
  };

  const hide = () => setPos(null);

  return (
    <>
      <div ref={triggerRef} className="inline-flex" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
        aria-describedby={pos ? tooltipId : undefined}>
        {children}
      </div>
      {pos && createPortal(
        <div
          className={cn(
            'fixed z-[9999] px-2 py-1 text-label-sm text-on-primary bg-inverse-surface rounded-cw-xs whitespace-nowrap pointer-events-none shadow-lg',
            side === 'top' && '-translate-x-1/2 -translate-y-[calc(100%+4px)]',
            side === 'bottom' && '-translate-x-1/2 translate-y-1',
            side === 'left' && '-translate-x-[calc(100%+4px)] -translate-y-1/2',
            side === 'right' && 'translate-x-1 -translate-y-1/2',
          )}
          style={{ left: pos.x, top: pos.y }}
          role="tooltip"
          id={tooltipId}
        >
          {content}
        </div>,
        document.body,
      )}
    </>
  );
}

export { Tooltip };
