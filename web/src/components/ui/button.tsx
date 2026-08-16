import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-cw-sm text-body-sm font-medium transition-all duration-short3 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer select-none active:scale-[0.97]',
  {
    variants: {
      variant: {
        default:
          'bg-primary text-on-primary hover:bg-primary/90 shadow-sm',
        secondary:
          'bg-secondary-container text-secondary hover:bg-secondary-container/80',
        outline:
          'border border-outline-variant bg-transparent text-on-surface hover:bg-surface-container hover:text-on-surface',
        ghost:
          'bg-transparent text-on-surface-variant hover:bg-surface-container hover:text-on-surface',
        destructive:
          'bg-error text-on-error hover:bg-error/90',
        link:
          'text-primary underline-offset-4 hover:underline bg-transparent',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-7 rounded-cw-xs px-2.5 text-label',
        lg: 'h-11 rounded-cw-md px-6 text-body',
        icon: 'h-8 w-8 p-0',
        'icon-sm': 'h-6 w-6 p-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
