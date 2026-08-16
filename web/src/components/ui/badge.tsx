import * as React from 'react';
import { cn } from '@/lib/utils';

/** Badge — status indicator */
interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info';
}

const variantClasses: Record<string, string> = {
  default: 'bg-secondary-container text-secondary',
  success: 'bg-track-audio/20 text-track-audio',
  warning: 'bg-track-text/20 text-track-text',
  error: 'bg-error/20 text-error',
  info: 'bg-primary-container text-on-primary-container',
};

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-cw-full px-2 py-0.5 text-label-sm font-medium',
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  );
}

export { Badge };
