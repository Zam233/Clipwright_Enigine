import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RotateCcw, Home, Copy, Check } from 'lucide-react';

interface Props { children: ReactNode; }
interface State { hasError: boolean; error: Error | null; copied: boolean; }

/**
 * ErrorBoundary — catches render crashes and shows a recoverable "render
 * failed" screen instead of a blank page, with reload / home / copy-details.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, copied: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, copied: false };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ClipWright] Render error:', error, info.componentStack);
  }

  private reload = () => window.location.reload();
  private goHome = () => { window.location.href = '/'; };

  private copyDetails = async () => {
    const { error } = this.state;
    if (!error) return;
    const text = `${error.name}: ${error.message}\n${error.stack ?? ''}`;
    try {
      await navigator.clipboard.writeText(text);
      this.setState({ copied: true });
      setTimeout(() => this.setState({ copied: false }), 1600);
    } catch { /* clipboard unavailable */ }
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    const { error, copied } = this.state;

    return (
      <div className="h-full w-full flex items-center justify-center bg-surface relative overflow-hidden">
        {/* ambient broken-signal backdrop */}
        <div className="absolute inset-0 pointer-events-none" aria-hidden>
          <div className="absolute inset-0 opacity-[0.05]" style={{
            backgroundImage: 'repeating-linear-gradient(0deg, #FF4444 0px, transparent 1px, transparent 3px)',
          }} />
          <div className="absolute -top-32 left-1/2 -translate-x-1/2 w-[700px] h-[400px] rounded-full opacity-[0.1]"
            style={{ background: 'radial-gradient(circle, #FF4444 0%, transparent 65%)' }} />
        </div>

        <div className="relative z-10 max-w-[520px] w-full mx-6">
          {/* signal-lost header */}
          <div className="flex items-center gap-3 mb-6">
            <span className="w-12 h-12 rounded-cw-md bg-error/15 border border-error/40 flex items-center justify-center shrink-0">
              <AlertTriangle className="w-6 h-6 text-error" />
            </span>
            <div>
              <p className="font-mono text-label-sm tracking-[0.3em] text-error uppercase">Render Interrupted</p>
              <h1 className="text-title font-bold text-on-surface">界面渲染中断</h1>
            </div>
          </div>

          <p className="text-body text-on-surface-variant leading-relaxed mb-5">
            编辑器遇到了一个未预期的错误。你的时间线数据保存在本地缓存中，重新加载后通常可以恢复。
          </p>

          {/* error detail */}
          {error && (
            <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden mb-6">
              <div className="flex items-center justify-between px-3 py-2 bg-surface-container-high border-b border-outline-variant/20">
                <span className="font-mono text-caption text-on-surface-variant">{error.name}</span>
                <button
                  onClick={this.copyDetails}
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

          {/* recovery actions */}
          <div className="flex gap-3">
            <button
              onClick={this.reload}
              className="flex-1 flex items-center justify-center gap-2 h-11 rounded-cw-sm bg-primary text-on-primary
                font-medium text-body-sm hover:bg-primary/90 active:scale-[0.98] transition-all cursor-pointer shadow-lg shadow-primary/20"
            >
              <RotateCcw className="w-4 h-4" /> 重新加载
            </button>
            <button
              onClick={this.goHome}
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
}
