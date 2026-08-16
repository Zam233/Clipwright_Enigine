import { useEffect, useRef, useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { pipelineApi } from '@/services/api';
import { cn } from '@/lib/utils';
import {
  Activity, Gauge, Coins, Timer, ChevronDown, ChevronRight,
  RefreshCw, FileJson, Loader2,
} from 'lucide-react';

interface Span { agent: string; start: number; dur: number; status: 'ok' | 'fail' | 'retry'; }
interface PipelineRun {
  id: string;
  topic: string;
  status: 'completed' | 'running' | 'failed';
  durationMs: number;
  agents: Span[];
  startedAt: string;
  llmCost?: number;
}
interface TraceView { loading: boolean; events: unknown[] | null; error?: string; }

const AGENT_COLORS: Record<string, string> = {
  structure: '#4F8CFF', material: '#A855F7', edit: '#FBBF24',
  animation: '#FF6B6B', audio: '#34D399', quality: '#F59E0B', self_heal: '#00E5FF',
};

/**
 * PipelineAdminPage — observability console: aggregate stats, run queue, and
 * a Gantt-style span trace per run showing each Agent's wall time.
 *
 * Data comes from the real backend (GET /api/pipeline/runs + /api/pipeline/trace/{id}).
 * When the backend is offline we surface an error banner — never fake data.
 */
export function PipelineAdminPage() {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [trace, setTrace] = useState<TraceView | null>(null);
  // Guard against stale in-flight trace responses when the user collapses/re-expands.
  const expandedRef = useRef<string | null>(null);

  const loadRuns = async () => {
    setLoading(true);
    setError(null);
    try {
      const records = await pipelineApi.getRunRecords();
      setRuns(normalize(records));
    } catch {
      setRuns([]);
      setError('后端离线：无法获取实时运行记录。启动后端后点击「刷新」重试。');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadRuns(); }, []);

  const toggleExpand = async (id: string) => {
    const next = expanded === id ? null : id;
    expandedRef.current = next;
    setExpanded(next);
    setTrace(null);
    if (next) {
      setTrace({ loading: true, events: null });
      try {
        const events = await pipelineApi.getTraceJson(next);
        if (expandedRef.current !== next) return; // 展开状态已变化，丢弃过期响应
        setTrace({ loading: false, events: Array.isArray(events) ? events : [] });
      } catch {
        if (expandedRef.current !== next) return;
        setTrace({ loading: false, events: null, error: '追踪不可用（后端离线或该记录的 trace 已清除）。' });
      }
    }
  };

  const completed = runs.filter((r) => r.status === 'completed');
  const runsWithCost = runs.filter((r) => r.llmCost !== undefined);
  const totalCost = runsWithCost.reduce((s, r) => s + (r.llmCost ?? 0), 0);
  const stats = {
    total: runs.length,
    successRate: runs.length ? Math.round((completed.length / runs.length) * 100) : 0,
    avgSec: completed.length ? (completed.reduce((s, r) => s + r.durationMs, 0) / completed.length / 1000).toFixed(1) : '0',
    // G9: 真实成本（后端记录 llm_cost）；不可得时显示 "—"，不造假值
    llmCost: runsWithCost.length > 0 ? totalCost.toFixed(2) : null,
  };
  // G9: 重试状态
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryErr, setRetryErr] = useState<string | null>(null);

  const retryRun = async (run: PipelineRun) => {
    const failedAgent = run.agents.find((s) => s.status === 'fail')?.agent ?? 'edit';
    setRetryingId(run.id);
    setRetryErr(null);
    try {
      await pipelineApi.retry(run.id, failedAgent);
      // 3s 后轮询刷新状态（B3 retry 为异步）
      setTimeout(() => { void loadRuns(); }, 3000);
    } catch (e) {
      setRetryErr(`重试失败：${(e as { message?: string })?.message ?? '未知错误'}`);
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <ConsoleShell>
      <div className="flex items-start justify-between gap-4">
        <ConsoleHeading kicker="Observability / Pipeline" title="管线监控"
          desc="追踪每次管线执行的 Agent 耗时分布、成功率与 LLM 成本，定位慢节点与失败重试。" />
        <button onClick={loadRuns} disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-cw-sm border border-outline-variant/30 bg-surface-container
            text-label-sm text-on-surface-variant hover:text-on-surface hover:border-outline/60 transition-colors disabled:opacity-50 cursor-pointer shrink-0">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          刷新
        </button>
      </div>

      {error && (
        <div className="flex items-center justify-between mb-5 max-w-[900px] bg-error/10 border border-error/30 rounded-cw-md px-4 py-2.5">
          <span className="text-label-sm text-error flex items-center gap-2">
            <Activity className="w-3.5 h-3.5" />{error}
          </span>
          <button onClick={loadRuns} className="text-label-sm text-error hover:text-error/80 cursor-pointer">重试</button>
        </div>
      )}

      {retryErr && (
        <div className="flex items-center justify-between mb-5 max-w-[900px] bg-error/10 border border-error/30 rounded-cw-md px-4 py-2.5">
          <span className="text-label-sm text-error flex items-center gap-2">
            <Activity className="w-3.5 h-3.5" />{retryErr}
          </span>
          <button onClick={() => setRetryErr(null)} className="text-label-sm text-error hover:text-error/80 cursor-pointer">关闭</button>
        </div>
      )}

      {/* stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5 mb-7 max-w-[900px]">
        <StatCard icon={Activity} label="总执行" value={String(stats.total)} sub="runs" color="#4F8CFF" />
        <StatCard icon={Gauge} label="成功率" value={`${stats.successRate}%`} sub="pass rate" color="#34D399" />
        <StatCard icon={Timer} label="平均耗时" value={stats.avgSec} sub="seconds" color="#FBBF24" />
        <StatCard icon={Coins} label="LLM 成本" value={stats.llmCost ? `¥${stats.llmCost}` : '—'} sub="actual" color="#A855F7" />
      </div>

      {/* run queue with gantt traces */}
      <div className="space-y-3 max-w-[900px]">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-20 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : runs.length === 0 ? (
          <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md px-5 py-10 text-center">
            <p className="text-body-sm text-on-surface-variant">暂无运行记录</p>
            <p className="text-caption text-on-surface-variant/60 mt-1 font-mono">GET /api/pipeline/runs 返回空列表 — 运行一次管线后此处会显示真实记录。</p>
          </div>
        ) : (
          runs.map((run) => {
            const isOpen = expanded === run.id;
            const total = run.durationMs || 1;
            return (
              <div key={run.id}
                className={cn('bg-surface-container border rounded-cw-md overflow-hidden transition-all duration-short3',
                  isOpen ? 'border-primary/40 shadow-lg shadow-primary/5' : 'border-outline-variant/30 hover:border-outline/60')}>
                {/* run header row */}
                <button onClick={() => toggleExpand(run.id)}
                  className="w-full flex items-center gap-3.5 px-5 py-3.5 text-left cursor-pointer">
                  {isOpen ? <ChevronDown className="w-4 h-4 text-primary shrink-0" /> : <ChevronRight className="w-4 h-4 text-on-surface-variant shrink-0" />}
                  <div className="flex-1 min-w-0">
                    <p className="text-body-sm font-medium text-on-surface truncate">{run.topic}</p>
                    <p className="font-mono text-caption text-on-surface-variant mt-0.5">{run.id} · {run.startedAt}</p>
                  </div>
                  <span className="font-mono text-caption text-on-surface-variant shrink-0">{(run.durationMs / 1000).toFixed(1)}s</span>
                  <StatusPill ok={run.status === 'completed'}
                    label={run.status === 'completed' ? 'DONE' : run.status === 'running' ? 'RUN' : 'FAIL'} />
                  {run.status === 'failed' && (
                    <button
                      onClick={(e) => { e.stopPropagation(); void retryRun(run); }}
                      disabled={retryingId === run.id}
                      className="ml-1 shrink-0 px-2.5 py-1 rounded-cw-xs bg-primary/15 text-primary text-label-sm
                        hover:bg-primary/25 transition-colors disabled:opacity-50 cursor-pointer flex items-center gap-1"
                    >
                      {retryingId === run.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                      重试
                    </button>
                  )}
                </button>

                {/* gantt trace */}
                {isOpen && (
                  <div className="px-5 pb-4">
                    <div className="border-t border-outline-variant/20 pt-3 space-y-1.5">
                      {run.agents.map((span, i) => {
                        const left = (span.start / total) * 100;
                        const width = Math.max(1.5, (span.dur / total) * 100);
                        const color = AGENT_COLORS[span.agent] ?? '#4F8CFF';
                        return (
                          <div key={i} className="flex items-center gap-3 group">
                            <span className="font-mono text-caption text-on-surface-variant w-20 shrink-0 text-right">{span.agent}</span>
                            <div className="flex-1 h-5 bg-surface rounded-cw-xs relative overflow-hidden">
                              <span
                                className={cn('absolute top-0.5 bottom-0.5 rounded-[3px] flex items-center px-1.5 transition-all duration-medium2',
                                  span.status === 'fail' && 'ring-1 ring-error')}
                                style={{ left: `${left}%`, width: `${width}%`, background: `${color}CC` }}
                                title={`${span.agent}: ${span.dur}ms`}
                              >
                                <span className="font-mono text-caption text-white/90 whitespace-nowrap overflow-hidden">
                                  {span.dur >= 100 ? `${(span.dur / 1000).toFixed(1)}s` : `${span.dur}ms`}
                                </span>
                              </span>
                            </div>
                            {span.status === 'retry' && <span className="font-mono text-caption text-track-caption shrink-0">↻</span>}
                            {span.status === 'fail' && <span className="font-mono text-caption text-error shrink-0">✕</span>}
                          </div>
                        );
                      })}
                    </div>
                    {/* legend */}
                    <div className="flex flex-wrap gap-3 mt-3 pt-2.5 border-t border-outline-variant/15">
                      {Object.entries(AGENT_COLORS).slice(0, 6).map(([agent, color]) => (
                        <span key={agent} className="flex items-center gap-1.5 font-mono text-caption text-on-surface-variant">
                          <i className="w-2.5 h-2.5 rounded-[2px]" style={{ background: color }} /> {agent}
                        </span>
                      ))}
                    </div>

                    {/* trace JSON viewer */}
                    <div className="mt-3 pt-2.5 border-t border-outline-variant/15">
                      <div className="flex items-center gap-2 mb-2">
                        <FileJson className="w-3.5 h-3.5 text-primary" />
                        <span className="text-label text-on-surface-variant">事件追踪 (JSON)</span>
                        {trace?.loading && <Loader2 className="w-3 h-3 animate-spin text-on-surface-variant" />}
                      </div>
                      {trace?.loading ? (
                        <div className="flex items-center gap-2 text-caption text-on-surface-variant py-3 font-mono">
                          <Loader2 className="w-3 h-3 animate-spin" /> 读取 trace…
                        </div>
                      ) : trace?.error ? (
                        <p className="text-caption text-warning font-mono">{trace.error}</p>
                      ) : (
                        <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2.5 font-mono text-caption
                          text-track-audio leading-relaxed max-h-72 overflow-auto whitespace-pre-wrap">
                          {JSON.stringify(trace?.events ?? [], null, 2)}
                        </pre>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </ConsoleShell>
  );
}

function StatCard({ icon: Icon, label, value, sub, color }: {
  icon: typeof Activity; label: string; value: string; sub: string; color: string;
}) {
  return (
    <div className="relative bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 overflow-hidden
      hover:border-outline/60 hover:-translate-y-0.5 transition-all duration-short3 group">
      <span className="absolute top-0 left-0 w-full h-[3px]" style={{ background: `linear-gradient(90deg, ${color}, transparent)` }} />
      <div className="flex items-center justify-between mb-2.5">
        <span className="text-label text-on-surface-variant">{label}</span>
        <span className="w-7 h-7 rounded-cw-xs flex items-center justify-center" style={{ background: `${color}1A`, color }}>
          <Icon className="w-3.5 h-3.5" />
        </span>
      </div>
      <p className="font-mono text-[26px] leading-none font-semibold text-on-surface">{value}</p>
      <p className="font-mono text-caption text-on-surface-variant/60 mt-1.5 uppercase tracking-wider">{sub}</p>
    </div>
  );
}

function normalize(data: unknown): PipelineRun[] {
  if (Array.isArray(data)) {
    return data.map((d, i) => {
      const o = d as Record<string, unknown>;
      const costRaw = (o as { llm_cost?: number | string }).llm_cost;
      const llmCost = typeof costRaw === 'number' ? costRaw
        : typeof costRaw === 'string' && costRaw !== '' ? Number(costRaw) : undefined;
      return {
        id: String(o.id ?? `pl_${i}`),
        topic: String(o.topic ?? '未命名'),
        status: (o.status as PipelineRun['status']) ?? 'completed',
        durationMs: Number(o.duration_ms ?? 0),
        startedAt: String(o.started_at ?? ''),
        agents: Array.isArray(o.agents) ? (o.agents as Span[]) : [],
        llmCost: Number.isFinite(llmCost) ? llmCost : undefined,
      };
    });
  }
  return [];
}
