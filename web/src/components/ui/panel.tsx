import * as React from 'react';
import { cn } from '@/lib/utils';

/** Panel — a surface container with optional header */
interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  actions?: React.ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  noPadding?: boolean;
}

const Panel = React.forwardRef<HTMLDivElement, PanelProps>(
  ({ className, title, actions, collapsible, defaultCollapsed, noPadding, children, ...props }, ref) => {
    const [collapsed, setCollapsed] = React.useState(defaultCollapsed ?? false);

    return (
      <div
        ref={ref}
        className={cn(
          'flex flex-col bg-surface-container border border-outline-variant/30 rounded-cw-sm overflow-hidden',
          className,
        )}
        {...props}
      >
        {title && (
          <div className="flex items-center justify-between px-3 py-2 border-b border-outline-variant/30 shrink-0">
            <div className="flex items-center gap-2">
              {collapsible && (
                <button
                  onClick={() => setCollapsed(!collapsed)}
                  className="text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer"
                >
                  <svg
                    className={cn('w-3.5 h-3.5 transition-transform duration-short3', collapsed && '-rotate-90')}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              )}
              <span className="text-title-sm font-medium text-on-surface">{title}</span>
            </div>
            {actions && <div className="flex items-center gap-1">{actions}</div>}
          </div>
        )}
        {!collapsed && (
          <div className={cn('flex-1 overflow-auto', !noPadding && 'p-3')}>
            {children}
          </div>
        )}
      </div>
    );
  },
);
Panel.displayName = 'Panel';

export { Panel };
