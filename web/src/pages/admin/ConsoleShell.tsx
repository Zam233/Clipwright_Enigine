import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from '@tanstack/react-router';
import { healthApi } from '@/services/api';
import { cn } from '@/lib/utils';
import {
  ArrowLeft, Cpu, Wrench, Puzzle, Shapes, LayoutTemplate, Webhook,
  Type, Activity, Terminal, GraduationCap, Clapperboard, Captions, Film,
} from 'lucide-react';

/**
 * ConsoleShell — the shared "system control room" for all admin pages.
 * Left rail navigation, live backend status, and a monospace telemetry strip.
 */
export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [backend, setBackend] = useState<'checking' | 'online' | 'offline'>('checking');
  const [uptime, setUptime] = useState(0);

  useEffect(() => {
    healthApi.check().then(() => setBackend('online')).catch(() => setBackend('offline'));
    const t = setInterval(() => setUptime((u) => u + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const NAV = [
    { to: '/settings/models', label: '模型测试', icon: Cpu, code: 'LLM' },
    { to: '/settings/tools', label: '工具与技能', icon: Wrench, code: 'TOOL' },
    { to: '/settings/plugins', label: '插件管理', icon: Puzzle, code: 'PLUG' },
    { to: '/settings/type-maker', label: '类型制作器', icon: Shapes, code: 'TYPE' },
    { to: '/settings/templates', label: '模板管理', icon: LayoutTemplate, code: 'TMPL' },
    { to: '/settings/learning', label: '学习训练', icon: GraduationCap, code: 'LRN' },
    { to: '/settings/video-editor', label: '视频编辑器', icon: Film, code: 'VEDT' },
    { to: '/settings/preprocess', label: '素材预处理', icon: Clapperboard, code: 'PREP' },
    { to: '/settings/subtitle-tools', label: '字幕与转写', icon: Captions, code: 'SUB' },
    { to: '/settings/webhooks', label: 'Webhook', icon: Webhook, code: 'HOOK' },
    { to: '/settings/fonts', label: '字体配置', icon: Type, code: 'FONT' },
    { to: '/pipeline-admin', label: '管线监控', icon: Activity, code: 'PIPE' },
  ];

  return (
    <div className="h-full flex flex-col bg-surface overflow-hidden relative">
      {/* ambient console backdrop */}
      <div className="absolute inset-0 pointer-events-none" aria-hidden>
        <div className="absolute inset-0 opacity-[0.04]" style={{
          backgroundImage: 'linear-gradient(to right, #8D8D99 1px, transparent 1px)',
          backgroundSize: '48px 100%',
        }} />
        <div className="absolute -top-40 right-0 w-[500px] h-[400px] rounded-full opacity-[0.07]"
          style={{ background: 'radial-gradient(circle, #4F6BED 0%, transparent 65%)' }} />
      </div>

      {/* console header */}
      <header className="relative z-10 flex items-center gap-3 px-5 py-3 border-b border-outline-variant/30 bg-surface-dim/80 shrink-0">
        <button onClick={() => navigate({ to: '/settings' })}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <span className="w-8 h-8 rounded-cw-sm bg-primary-container flex items-center justify-center">
          <Terminal className="w-4 h-4 text-on-primary-container" />
        </span>
        <div className="leading-tight">
          <h1 className="text-title-sm font-bold text-on-surface tracking-wide">系统控制台</h1>
          <p className="font-mono text-caption text-on-surface-variant tracking-[0.2em]">CLIPWRIGHT · CONTROL ROOM</p>
        </div>

        {/* telemetry strip */}
        <div className="ml-auto flex items-center gap-4 font-mono text-caption text-on-surface-variant">
          <span className="hidden md:flex items-center gap-1.5">
            <i className={cn('w-1.5 h-1.5 rounded-full',
              backend === 'online' ? 'bg-track-audio animate-pulse' : backend === 'offline' ? 'bg-error' : 'bg-track-text animate-pulse')} />
            {backend === 'online' ? 'ENGINE:OK' : backend === 'offline' ? 'ENGINE:OFFLINE' : 'ENGINE:…'}
          </span>
          <span className="hidden lg:inline">SESSION {formatUptime(uptime)}</span>
          <span className="text-primary">v0.1.0</span>
        </div>
      </header>

      <div className="relative z-10 flex flex-1 overflow-hidden">
        {/* nav rail */}
        <nav className="w-[190px] shrink-0 border-r border-outline-variant/25 py-3 px-2 overflow-y-auto">
          {NAV.map(({ to, label, icon: Icon, code }) => {
            const active = location.pathname === to;
            return (
              <button
                key={to}
                onClick={() => navigate({ to })}
                className={cn(
                  'w-full flex items-center gap-2.5 px-3 py-2.5 rounded-cw-sm mb-0.5 text-left transition-all duration-short3 cursor-pointer group',
                  active ? 'bg-primary/12 text-primary border border-primary/30'
                    : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container border border-transparent',
                )}
              >
                <Icon className={cn('w-4 h-4 shrink-0', active ? 'text-primary' : 'text-on-surface-variant group-hover:text-on-surface')} />
                <span className="text-body-sm font-medium flex-1">{label}</span>
                <span className={cn('font-mono text-caption', active ? 'text-primary/70' : 'text-on-surface-variant/40')}>{code}</span>
              </button>
            );
          })}
        </nav>

        {/* content */}
        <main className="flex-1 overflow-y-auto p-6 min-w-0">
          {children}
        </main>
      </div>
    </div>
  );
}

function formatUptime(s: number): string {
  const m = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

/** Shared console section header. */
export function ConsoleHeading({ kicker, title, desc }: { kicker: string; title: string; desc?: string }) {
  return (
    <div className="mb-6">
      <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-1">{kicker}</p>
      <h2 className="text-headline font-bold text-on-surface">{title}</h2>
      {desc && <p className="text-body text-on-surface-variant mt-1 max-w-[560px]">{desc}</p>}
    </div>
  );
}

/** Shared status pill. */
export function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-cw-full font-mono text-caption border',
      ok ? 'bg-track-audio/10 text-track-audio border-track-audio/30' : 'bg-error/10 text-error border-error/30')}>
      <i className={cn('w-1.5 h-1.5 rounded-full', ok ? 'bg-track-audio animate-pulse' : 'bg-error')} />
      {label}
    </span>
  );
}
