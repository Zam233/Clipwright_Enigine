import { useEffect, useMemo, useState } from 'react';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { preprocessApi } from '@/services/api';
import type { PreprocessTask } from '@/services/api/preprocess';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  ChevronDown,
  ChevronRight,
  Clapperboard,
  Eye,
  FileDown,
  Layers,
  ListChecks,
  Loader2,
  RefreshCw,
  Send,
  XCircle,
} from 'lucide-react';

/** 操作 → 颜色映射（后端 SUPPORTED_OPERATIONS） */
const OP_COLORS: Record<string, string> = {
  metadata: '#4F8CFF',
  scenes: '#A855F7',
  thumbnail: '#FBBF24',
  audio: '#34D399',
  bpm: '#FF6B6B',
  transcribe: '#00E5FF',
};

/** 后端离线时的操作兜底（与 clipwright/api/preprocess.py 的 SUPPORTED_OPERATIONS 一致） */
const DEMO_OPERATIONS = ['metadata', 'scenes', 'thumbnail', 'audio', 'bpm', 'transcribe'];
const DEMO_DESCRIPTIONS: Record<string, string> = {
  metadata: '提取视频元数据 (分辨率、帧率、时长、编码格式)',
  scenes: '场景检测 (基于内容变化的切点检测)',
  thumbnail: '生成缩略图和预览帧序列',
  audio: '提取音频轨道',
  bpm: '音频节拍检测 (BPM)',
  transcribe: '语音转文字 (Whisper STT)',
};

const STATUS_META: Record<PreprocessTask['status'], { label: string; badge: 'success' | 'warning' | 'error' | 'info' }> = {
  completed: { label: 'DONE', badge: 'success' },
  running: { label: 'RUN', badge: 'info' },
  queued: { label: 'QUEUED', badge: 'warning' },
  failed: { label: 'FAIL', badge: 'error' },
};

/**
 * PreprocessPage — 素材预处理控制台。
 * 操作列表 · 提交（文件路径+操作）· 批量提交 · 队列 · 任务详情 · 结果 · 取消。
 * 覆盖后端 /api/preprocess/* 全流程（含 DELETE 取消，仅限 queued）。
 */
export function PreprocessPage() {
  const [operations, setOperations] = useState<string[]>(DEMO_OPERATIONS);
  const [descriptions, setDescriptions] = useState<Record<string, string>>(DEMO_DESCRIPTIONS);
  const [online, setOnline] = useState<boolean | null>(null);

  const [queue, setQueue] = useState<PreprocessTask[]>([]);
  const [statusFilter, setStatusFilter] = useState<PreprocessTask['status'] | ''>('');
  const [selected, setSelected] = useState<string | null>(null);
  const [resultContent, setResultContent] = useState<string | null>(null);

  const [filePath, setFilePath] = useState('');
  const [batchPaths, setBatchPaths] = useState('');
  const [selectedOps, setSelectedOps] = useState<string[]>(['metadata', 'scenes', 'thumbnail']);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  // 操作列表
  useEffect(() => {
    let alive = true;
    preprocessApi
      .listOperations()
      .then((res) => {
        if (!alive) return;
        if (Array.isArray(res.operations) && res.operations.length) setOperations(res.operations);
        if (res.descriptions) setDescriptions(res.descriptions);
        setOnline(true);
      })
      .catch(() => {
        if (alive) setOnline(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 队列轮询（含选中任务同步 + 结果回填）
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const q = await preprocessApi.listQueue(statusFilter);
        if (!alive) return;
        setQueue(q);
        setOnline(true);
        const cur = q.find((t) => t.task_id === selected) ?? null;
        if (cur && (cur.status === 'completed' || cur.status === 'failed')) {
          const hasResults = cur.results && Object.keys(cur.results).length > 0;
          setResultContent((prev) => (hasResults ? JSON.stringify(cur.results, null, 2) : prev));
        }
      } catch {
        if (alive) {
          setQueue([]);
          setOnline(false);
        }
      }
    };
    refresh();
    const t = setInterval(refresh, 2500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [statusFilter, selected]);

  const toggleOp = (op: string) =>
    setSelectedOps((prev) => (prev.includes(op) ? prev.filter((o) => o !== op) : [...prev, op]));

  const flash = (kind: 'ok' | 'err', text: string) => setNotice({ kind, text });

  const submit = async () => {
    const path = filePath.trim();
    if (!path) return flash('err', '请输入文件路径');
    if (!selectedOps.length) return flash('err', '请选择至少一个预处理操作');
    setBusy(true);
    try {
      const task = await preprocessApi.submit(path, selectedOps);
      flash('ok', `已提交「${task.file_name}」 → ${task.task_id}`);
      setFilePath('');
      setSelected(task.task_id);
      setResultContent(null);
      const q = await preprocessApi.listQueue(statusFilter);
      setQueue(q);
    } catch (e) {
      flash('err', `提交失败：${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const batchSubmit = async () => {
    const paths = batchPaths.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!paths.length) return flash('err', '请输入至少一个文件路径（每行一个）');
    if (!selectedOps.length) return flash('err', '请选择至少一个预处理操作');
    setBusy(true);
    try {
      const tasks = await preprocessApi.batchSubmit(paths, selectedOps);
      flash('ok', `批量提交完成：${tasks.length}/${paths.length} 个文件入队`);
      setBatchPaths('');
      setResultContent(null);
      const q = await preprocessApi.listQueue(statusFilter);
      setQueue(q);
    } catch (e) {
      flash('err', `批量提交失败：${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const showResults = async (task: PreprocessTask) => {
    try {
      const res = await preprocessApi.listResults(task.task_id);
      setResultContent(JSON.stringify(res, null, 2));
      setSelected(task.task_id);
    } catch (e) {
      flash('err', `读取结果失败：${errText(e)}`);
    }
  };

  const cancel = async (task: PreprocessTask) => {
    try {
      await preprocessApi.removeTask(task.task_id);
      flash('ok', `已取消 ${task.task_id}`);
      const q = await preprocessApi.listQueue(statusFilter);
      setQueue(q);
    } catch (e) {
      flash('err', `取消失败：${errText(e)}`);
    }
  };

  const opsDefs = useMemo(
    () => operations.map((op) => ({ name: op, desc: descriptions[op] ?? '' })),
    [operations, descriptions],
  );

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Media / Preprocess" title="素材预处理"
        desc="对素材执行元数据提取、场景检测、缩略图、音频/BPM 与转写等预处理，任务在后台队列异步执行。" />

      {/* offline banner */}
      {online === false && (
        <div className="mb-5 max-w-[900px] flex items-center gap-3 bg-error/10 border border-error/30 rounded-cw-md px-4 py-2.5">
          <XCircle className="w-4 h-4 text-error shrink-0" />
          <span className="text-body-sm text-error">后端离线 —— 当前展示兜底操作列表，队列为空。启动引擎后可进行真实往返。</span>
        </div>
      )}

      {/* notice */}
      {notice && (
        <div className={cn('mb-5 max-w-[900px] rounded-cw-md px-4 py-2.5 border font-mono text-caption',
          notice.kind === 'ok'
            ? 'bg-track-audio/10 border-track-audio/30 text-track-audio'
            : 'bg-error/10 border-error/30 text-error')}>
          {notice.text}
        </div>
      )}

      {/* operations + submit */}
      <div className="grid grid-cols-12 gap-5 mb-7">
        <div className="col-span-12 lg:col-span-7 space-y-4">
          {/* operation selector */}
          <section className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20">
              <ListChecks className="w-4 h-4 text-primary" />
              <h3 className="text-title-sm font-medium text-on-surface">预处理操作</h3>
              <span className="ml-auto font-mono text-caption text-on-surface-variant">选中 {selectedOps.length}</span>
            </header>
            <div className="p-4 space-y-2">
              {opsDefs.map((op) => (
                <button key={op.name} onClick={() => toggleOp(op.name)}
                  className={cn('w-full flex items-center gap-2.5 px-3 py-2 rounded-cw-sm border text-left transition-all duration-short3 cursor-pointer group',
                    selectedOps.includes(op.name)
                      ? 'border-primary/50 bg-primary/10'
                      : 'border-outline-variant/25 bg-surface hover:border-outline/50')}>
                  <span className={cn('w-4 h-4 rounded-cw-xs border flex items-center justify-center shrink-0',
                    selectedOps.includes(op.name) ? 'bg-primary border-primary' : 'border-outline')}>
                    {selectedOps.includes(op.name) && <span className="w-1.5 h-1.5 rounded-[1px] bg-on-primary" />}
                  </span>
                  <i className="w-2.5 h-2.5 rounded-[2px] shrink-0" style={{ background: OP_COLORS[op.name] ?? '#8D8D99' }} />
                  <span className={cn('font-mono text-body-sm shrink-0', selectedOps.includes(op.name) ? 'text-primary' : 'text-on-surface')}>{op.name}</span>
                  {op.desc && <span className="text-caption text-on-surface-variant truncate">{op.desc}</span>}
                </button>
              ))}
            </div>
          </section>

          {/* single submit */}
          <section className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20">
              <Send className="w-4 h-4 text-primary" />
              <h3 className="text-title-sm font-medium text-on-surface">提交单个任务</h3>
            </header>
            <div className="p-4 space-y-3">
              <label className="block">
                <span className="block text-label text-on-surface-variant mb-1.5">文件路径</span>
                <input
                  value={filePath}
                  onChange={(e) => setFilePath(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
                  placeholder="如 D:\media\demo.mp4 或 /library/source/xxx.mp4"
                  className="w-full bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
                    outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40"
                />
              </label>
              <Button onClick={submit} disabled={busy} className="w-full">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                {busy ? '提交中…' : '提交任务'}
              </Button>
            </div>
          </section>

          {/* batch submit */}
          <section className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20">
              <Layers className="w-4 h-4 text-primary" />
              <h3 className="text-title-sm font-medium text-on-surface">批量提交</h3>
            </header>
            <div className="p-4 space-y-3">
              <label className="block">
                <span className="block text-label text-on-surface-variant mb-1.5">文件路径（每行一个）</span>
                <textarea
                  value={batchPaths}
                  onChange={(e) => setBatchPaths(e.target.value)}
                  rows={4}
                  placeholder={'D:\\media\\clip_01.mp4\nD:\\media\\clip_02.mp4\nD:\\media\\clip_03.mp4'}
                  className="w-full bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
                    outline-none border border-outline-variant/30 focus:border-primary resize-y placeholder:text-on-surface-variant/40"
                />
              </label>
              <Button onClick={batchSubmit} disabled={busy} variant="outline" className="w-full">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Layers className="w-4 h-4" />}
                {busy ? '提交中…' : '批量提交'}
              </Button>
            </div>
          </section>
        </div>

        {/* queue */}
        <div className="col-span-12 lg:col-span-5">
          <section className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20">
              <Clapperboard className="w-4 h-4 text-primary" />
              <h3 className="text-title-sm font-medium text-on-surface">任务队列</h3>
              <span className="ml-auto font-mono text-caption text-on-surface-variant">{queue.length} 个</span>
            </header>

            {/* status filter tabs */}
            <div className="flex gap-1 px-4 pt-3">
              {(['', 'queued', 'running', 'completed', 'failed'] as const).map((s) => (
                <button key={s} onClick={() => setStatusFilter(s)}
                  className={cn('px-2.5 py-1 rounded-cw-xs font-mono text-caption transition-colors cursor-pointer',
                    statusFilter === s ? 'bg-primary text-on-primary' : 'text-on-surface-variant hover:text-on-surface bg-surface')}>
                  {s === '' ? '全部' : STATUS_META[s].label}
                </button>
              ))}
            </div>

            <div className="p-4 space-y-2.5 max-h-[560px] overflow-y-auto">
              {queue.length === 0 ? (
                <div className="text-center py-10">
                  <Clapperboard className="w-8 h-8 mx-auto text-on-surface-variant/30 mb-2" />
                  <p className="font-mono text-caption text-on-surface-variant/60">QUEUE EMPTY</p>
                </div>
              ) : (
                queue.map((task) => {
                  const isOpen = selected === task.task_id;
                  const meta = STATUS_META[task.status];
                  return (
                    <div key={task.task_id}
                      className={cn('bg-surface rounded-cw-md border overflow-hidden transition-all duration-short3',
                        isOpen ? 'border-primary/40 shadow-lg shadow-primary/5' : 'border-outline-variant/25 hover:border-outline/60')}>
                      <div className="px-3.5 py-2.5">
                        <div className="flex items-center gap-2">
                          <button onClick={() => { setSelected(isOpen ? null : task.task_id); setResultContent(null); }}
                            className="flex-1 min-w-0 text-left cursor-pointer">
                            <p className="text-body-sm font-medium text-on-surface truncate">{task.file_name}</p>
                            <p className="font-mono text-caption text-on-surface-variant mt-0.5 truncate">{task.task_id}</p>
                          </button>
                          <span className={cn('px-2 py-0.5 rounded-cw-full font-mono text-caption border shrink-0',
                            meta.badge === 'success' && 'bg-track-audio/10 text-track-audio border-track-audio/30',
                            meta.badge === 'info' && 'bg-primary/10 text-primary border-primary/30',
                            meta.badge === 'warning' && 'bg-track-text/10 text-track-text border-track-text/30',
                            meta.badge === 'error' && 'bg-error/10 text-error border-error/30')}>
                            {meta.label}
                          </span>
                        </div>

                        {/* progress bar */}
                        <div className="mt-2 flex items-center gap-2">
                          <div className="flex-1 h-1.5 bg-surface-container-high rounded-cw-full overflow-hidden">
                            <div className="h-full rounded-cw-full transition-all duration-medium2"
                              style={{
                                width: `${Math.max(2, task.progress)}%`,
                                background: task.status === 'failed' ? 'var(--color-error)' : '#4F6BED',
                              }} />
                          </div>
                          <span className="font-mono text-caption text-on-surface-variant w-9 text-right">{task.progress}%</span>
                        </div>

                        <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                          {task.operations.map((op) => (
                            <span key={op} className="font-mono text-caption px-1.5 py-0.5 rounded-cw-xs"
                              style={{ background: `${OP_COLORS[op] ?? '#8D8D99'}1A`, color: OP_COLORS[op] ?? '#8D8D99' }}>
                              {op}
                            </span>
                          ))}
                        </div>

                        {task.error && (
                          <p className="mt-2 font-mono text-caption text-error break-words">{task.error}</p>
                        )}

                        <div className="mt-2.5 flex items-center gap-2">
                          <Button size="sm" variant="ghost" onClick={() => { setSelected(task.task_id); setResultContent(null); }}>
                            {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                            详情
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => showResults(task)} disabled={task.status !== 'completed'}>
                            <FileDown className="w-3.5 h-3.5" />
                            结果
                          </Button>
                          {task.status === 'queued' && (
                            <Button size="sm" variant="ghost" onClick={() => cancel(task)} className="text-error hover:text-error">
                              <XCircle className="w-3.5 h-3.5" />
                              取消
                            </Button>
                          )}
                        </div>

                        {isOpen && (
                          <pre className="mt-2 bg-surface-container-high rounded-cw-sm px-3 py-2.5 font-mono text-caption
                            text-on-surface-variant leading-relaxed max-h-64 overflow-auto whitespace-pre-wrap border border-outline-variant/20">
                            {JSON.stringify(task, null, 2)}
                          </pre>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* results viewer */}
            {resultContent && (
              <div className="border-t border-outline-variant/20 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Eye className="w-4 h-4 text-primary" />
                  <span className="font-mono text-label-sm text-on-surface">RESULTS</span>
                  <button onClick={() => setResultContent(null)}
                    className="ml-auto font-mono text-caption text-on-surface-variant hover:text-on-surface cursor-pointer">
                    关闭
                  </button>
                </div>
                <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2.5 font-mono text-caption
                  text-track-audio leading-relaxed max-h-56 overflow-auto whitespace-pre-wrap">{resultContent}</pre>
              </div>
            )}
          </section>
        </div>
      </div>

      {/* supported operations reference */}
      <div className="max-w-[900px]">
        <button onClick={() => preprocessApi.listOperations().then((r) => {
          if (Array.isArray(r.operations) && r.operations.length) {
            setOperations(r.operations);
            if (r.descriptions) setDescriptions(r.descriptions);
            setOnline(true);
          }
        }).catch(() => setOnline(false))}
          className="inline-flex items-center gap-1.5 font-mono text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer">
          <RefreshCw className="w-3.5 h-3.5" /> 刷新操作列表
        </button>
      </div>
    </ConsoleShell>
  );
}

function errText(e: unknown): string {
  if (typeof e === 'string') return e;
  if (e instanceof Error) return e.message;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}
