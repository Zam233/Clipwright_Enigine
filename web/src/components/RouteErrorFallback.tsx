import { useRouter } from '@tanstack/react-router';
import { AlertTriangle, RotateCcw, Home, Copy, Check } from 'lucide-react';
import { useState } from 'react';

/**
 * RouteErrorFallback — W8: 每条路由独立的错误边界（TanStack Router
 * errorComponent）。页面渲染抛错时展示可恢复的错误页，而不让整个 SPA 白屏。
 *
 * TanStack Router 会把 {error, reset} 作为 props 传入 errorComponent。
 */
export function RouteErrorFallback({ error, reset }: { error: Error; reset?: () => void }) {
  const router = useRouter();
  const [copied, setCopied] = useState(false);

  const copyDetails = async () => {
    const text = `${error?.name ?? 'Error'}: ${error?.message ?? ''}\n${error?.stack ?? ''}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="h-full w-full flex items-center justify-center bg-surface relative overflow-hidden">
      <div className="absolute inset-0 pointer-events-none" aria-hidden>
        <div className="absolute inset-0 opacity-[0.05]" style={{
          backgroundImage: 'repeating-linear-gradient(0deg, #FF4444 0px, transparent 1px, transparent 3px)',
        }} />
      </div>

      <div className="relative z-10 max-w-[520px] w-full mx-6">
        <div className="flex items-center gap-3 mb-6">
          <span className="w-12 h-12 rounded-cw-md bg-error/15 border border-error/40 flex items-center justify-center shrink-0">
            <AlertTriangle className="w-6 h-6 text-error" />
          </span>
          <div>
            <p className="font-mono text-label-sm tracking-[0.3em] text-error uppercase">Route Interrupted</p>
            <h1 className="text-title font-bold text-on-surface">此页面渲染失败</h1>
          </div>
        </div>

        <p className="text-body text-on-surface-variant leading-relaxed mb-5">
          该页面遇到了未预期的错误。你可以重试渲染，或返回工作台继续其他操作。
        </p>

        {error && (
          <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden mb-6">
            <div className="flex items-center justify-between px-3 py-2 bg-surface-container-high border-b border-outline-variant/20">
              <span className="font-mono text-caption text-on-surface-variant">{error.name ?? 'Error'}</span>
              <button
                onClick={copyDetails}
                className="flex items-center gap-1 text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer"
              >
                {copied ? <Check className="w-3 h-3 text-track-audio" /> : <Copy className="w-3 h-3" />}
                {copied ? '已复制' : '复制详情'}
              </button>
            </div>
            <pre className="px-3 py-2.5 font-mono text-caption text-error/90 whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto">
              {error.message}
            </pre>
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={() => { reset?.(); }}
            className="flex-1 flex items-center justify-center gap-2 h-11 rounded-cw-sm bg-primary text-on-primary
              font-medium text-body-sm hover:bg-primary/90 active:scale-[0.98] transition-all cursor-pointer shadow-lg shadow-primary/20"
          >
            <RotateCcw className="w-4 h-4" /> 重试渲染
          </button>
          <button
            onClick={() => router.navigate({ to: '/' })}
            className="flex-1 flex items-center justify-center gap-2 h-11 rounded-cw-sm border border-outline-variant/50
              text-on-surface font-medium text-body-sm hover:bg-surface-container active:scale-[0.98] transition-all cursor-pointer"
          >
            <Home className="w-4 h-4" /> 返回工作台
          </button>
        </div>
      </div>
    </div>
  );
}
