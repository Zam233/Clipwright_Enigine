import React from 'react';
import { cn } from '@/lib/utils';

interface StandardLayoutProps {
  title?: string;
  children: React.ReactNode;
  className?: string;
  /** 可选的页内子导航条（渲染在标题栏之下、内容区之上） */
  nav?: React.ReactNode;
}

/** StandardLayout — for non-editor pages (settings, persona list, etc.) */
export function StandardLayout({ title, children, className, nav }: StandardLayoutProps) {
  return (
    <div className="flex flex-col h-full w-full bg-surface">
      {title && (
        <header className="flex items-center gap-4 px-6 py-4 border-b border-outline-variant/30 shrink-0">
          <a
            href="/"
            className="text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </a>
          <h1 className="text-headline text-on-surface">{title}</h1>
        </header>
      )}
      {nav && (
        <nav className="flex items-center gap-1 px-6 py-2 border-b border-outline-variant/30 shrink-0 overflow-x-auto">
          {nav}
        </nav>
      )}
      <main className={cn('flex-1 overflow-auto p-6', className)}>{children}</main>
    </div>
  );
}
