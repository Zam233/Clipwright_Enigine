import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { learningApi } from '@/services/api';
import type { DatasetInfo, LearningStatus, TrainingJob } from '@/services/api/learning';
import { Button, Badge } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  GraduationCap, Database, Plus, Trash2, Play, Square, Boxes, RefreshCw, Gauge,
} from 'lucide-react';

const TERMINAL_STATUSES: Array<TrainingJob['status']> = ['completed', 'failed', 'cancelled'];

/**
 * LearningPage — LoRA 微调管线控制台。Backed by /api/learning/*
 * (status / datasets CRUD / jobs CRUD+start+cancel / models). Operates the
 * backend training pipeline: manage datasets, queue/start/cancel training
 * jobs, and inspect finished models.
 */
export function LearningPage() {
  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [models, setModels] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [notice, setNotice] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const [newDsName, setNewDsName] = useState('');
  const [newDsDesc, setNewDsDesc] = useState('');
  const [newJobName, setNewJobName] = useState('');
  const [newJobDataset, setNewJobDataset] = useState('');
  const [newJobBaseModel, setNewJobBaseModel] = useState('Qwen2.5-VL-7B');
  const [newJobEpochs, setNewJobEpochs] = useState('3');

  const reloadStatus = async () => {
    try {
      setStatus(await learningApi.status());
    } catch { /* offline → keep previous/null */ }
  };

  const reloadDatasets = async () => {
    try {
      const list = await learningApi.listDatasets();
      setDatasets(Array.isArray(list) ? list : []);
    } catch {
      setNotice('数据集加载失败：后端不可达');
    }
  };

  const reloadJobs = async () => {
    try {
      const list = await learningApi.listJobs();
      setJobs(Array.isArray(list) ? list : []);
    } catch {
      setNotice('训练任务加载失败：后端不可达');
    }
  };

  const reloadModels = async () => {
    try {
      const list = await learningApi.listModels();
      setModels(Array.isArray(list) ? list : []);
    } catch {
      setNotice('模型列表加载失败：后端不可达');
    }
  };

  const refreshAll = async () => {
    setNotice('');
    await Promise.allSettled([reloadStatus(), reloadDatasets(), reloadJobs(), reloadModels()]);
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      await reloadStatus();
      await reloadDatasets();
      if (alive) setLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      await reloadJobs();
      if (alive) setJobsLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      await reloadModels();
      if (alive) setModelsLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  const createDataset = async () => {
    if (!newDsName.trim()) return;
    try {
      await learningApi.createDataset({ name: newDsName.trim(), description: newDsDesc.trim() });
      setNewDsName('');
      setNewDsDesc('');
      setNotice('');
      await reloadDatasets();
    } catch {
      setNotice('创建数据集失败：后端不可达');
    }
  };

  const deleteDataset = async (id: string) => {
    setBusyId(`ds_${id}`);
    try {
      await learningApi.deleteDataset(id);
      setDatasets((ds) => ds.filter((d) => d.dataset_id !== id));
    } catch {
      setNotice('删除数据集失败：后端不可达');
    }
    setBusyId(null);
  };

  const createJob = async () => {
    if (!newJobName.trim() || !newJobDataset) return;
    try {
      await learningApi.createJob({
        name: newJobName.trim(),
        dataset_id: newJobDataset,
        base_model: newJobBaseModel.trim() || undefined,
        epochs: Number(newJobEpochs) > 0 ? Number(newJobEpochs) : undefined,
      });
      setNewJobName('');
      setNotice('');
      await reloadJobs();
    } catch {
      setNotice('创建训练任务失败：后端不可达');
    }
  };

  const startJob = async (id: string) => {
    setBusyId(`start_${id}`);
    try {
      await learningApi.startJob(id);
      await reloadJobs();
    } catch {
      setNotice('启动任务失败：后端不可达');
    }
    setBusyId(null);
  };

  const cancelJob = async (id: string) => {
    setBusyId(`cancel_${id}`);
    try {
      await learningApi.cancelJob(id);
      await reloadJobs();
    } catch {
      setNotice('取消任务失败：后端不可达');
    }
    setBusyId(null);
  };

  const deleteJob = async (id: string) => {
    setBusyId(`del_${id}`);
    try {
      await learningApi.deleteJob(id);
      setJobs((js) => js.filter((j) => j.job_id !== id));
    } catch {
      setNotice('删除任务失败：后端不可达');
    }
    setBusyId(null);
  };

  const trainingActive = status?.status === 'training';

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Training / Learning" title="学习训练"
        desc="LoRA 微调管线控制台：数据集管理、训练任务队列与已训练模型检视。训练在后端 GPU 上异步执行。" />

      {notice && (
        <div className="flex items-center gap-2 bg-error/10 border border-error/30 rounded-cw-sm px-3.5 py-2 mb-4 max-w-[960px]">
          <span className="font-mono text-caption text-error">{notice}</span>
        </div>
      )}

      {/* backend status strip */}
      <div className="flex flex-wrap items-center gap-2 mb-6 max-w-[960px]">
        <StatusPill ok={!!status} label={status ? `ENGINE:${status.status.toUpperCase()}` : 'ENGINE:OFFLINE'} />
        {status && (
          <>
            <StatusPill ok={status.gpu_available} label={`GPU:${status.gpu_available ? 'ON' : 'OFF'}`} />
            <span className="font-mono text-caption text-on-surface-variant">
              ACTIVE {status.active_jobs} / TOTAL {status.total_jobs}
            </span>
            {trainingActive && (
              <span className="font-mono text-caption text-track-audio animate-pulse flex items-center gap-1">
                <Gauge className="w-3 h-3" /> 训练进行中…
              </span>
            )}
          </>
        )}
        <Button size="sm" variant="outline" onClick={refreshAll} className="ml-auto">
          <RefreshCw className="w-3.5 h-3.5" /> 刷新
        </Button>
      </div>

      {/* datasets */}
      <section className="mb-9 max-w-[960px]">
        <h3 className="flex items-center gap-2 text-title-sm font-semibold text-on-surface mb-4">
          <Database className="w-4 h-4 text-primary" /> 训练数据集
          <span className="font-mono text-caption text-on-surface-variant font-normal">{datasets.length}</span>
        </h3>

        <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 mb-4">
          <p className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-3">
            <Plus className="w-3.5 h-3.5" /> 新建数据集
          </p>
          <div className="flex flex-wrap gap-2">
            <input value={newDsName} onChange={(e) => setNewDsName(e.target.value)}
              placeholder="数据集名称"
              className="flex-1 min-w-[160px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
            <input value={newDsDesc} onChange={(e) => setNewDsDesc(e.target.value)}
              placeholder="描述（可选）"
              className="flex-1 min-w-[160px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
            <Button size="sm" onClick={createDataset} disabled={!newDsName.trim()}>创建</Button>
          </div>
        </div>

        {loading ? (
          Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-14 bg-surface-container rounded-cw-md animate-pulse mb-2.5" />
          ))
        ) : datasets.length === 0 ? (
          <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-8 text-center">
            <Database className="w-7 h-7 text-on-surface-variant/40 mx-auto mb-2" />
            <p className="text-body-sm text-on-surface-variant">暂无数据集</p>
          </div>
        ) : (
          datasets.map((d) => (
            <div key={d.dataset_id}
              className="flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-3.5 mb-2.5
                hover:border-outline/60 transition-colors duration-short3 group">
              <span className="w-9 h-9 rounded-cw-sm bg-track-video/15 text-track-video flex items-center justify-center shrink-0">
                <Database className="w-4.5 h-4.5" />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-body-sm font-semibold text-on-surface truncate">{d.name}</p>
                <p className="font-mono text-caption text-on-surface-variant mt-0.5 truncate">
                  {d.dataset_id}
                  {d.sample_count > 0 && ` · ${d.sample_count} 样本`}
                  {d.total_duration_sec > 0 && ` · ${d.total_duration_sec.toFixed(1)}s`}
                </p>
              </div>
              <button onClick={() => deleteDataset(d.dataset_id)} disabled={busyId === `ds_${d.dataset_id}`} title="删除数据集"
                className="p-2 rounded-cw-xs text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors cursor-pointer disabled:opacity-50">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))
        )}
      </section>

      {/* training jobs */}
      <section className="mb-9 max-w-[960px]">
        <h3 className="flex items-center gap-2 text-title-sm font-semibold text-on-surface mb-4">
          <GraduationCap className="w-4 h-4 text-primary" /> 训练任务
          <span className="font-mono text-caption text-on-surface-variant font-normal">{jobs.length}</span>
        </h3>

        <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 mb-4">
          <p className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-3">
            <Plus className="w-3.5 h-3.5" /> 新建训练任务
          </p>
          <div className="flex flex-wrap gap-2 mb-3">
            <input value={newJobName} onChange={(e) => setNewJobName(e.target.value)}
              placeholder="任务名称"
              className="flex-1 min-w-[140px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
            <select value={newJobDataset} onChange={(e) => setNewJobDataset(e.target.value)}
              className="flex-1 min-w-[140px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary">
              <option value="">选择数据集…</option>
              {datasets.map((d) => (
                <option key={d.dataset_id} value={d.dataset_id}>{d.name}</option>
              ))}
            </select>
            <input value={newJobBaseModel} onChange={(e) => setNewJobBaseModel(e.target.value)}
              placeholder="基座模型"
              className="flex-1 min-w-[140px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
            <input value={newJobEpochs} onChange={(e) => setNewJobEpochs(e.target.value)}
              placeholder="Epochs"
              className="w-20 bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
            <Button size="sm" onClick={createJob} disabled={!newJobName.trim() || !newJobDataset}>创建</Button>
          </div>
        </div>

        {jobsLoading ? (
          Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-16 bg-surface-container rounded-cw-md animate-pulse mb-2.5" />
          ))
        ) : jobs.length === 0 ? (
          <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-8 text-center">
            <GraduationCap className="w-7 h-7 text-on-surface-variant/40 mx-auto mb-2" />
            <p className="text-body-sm text-on-surface-variant">暂无训练任务</p>
          </div>
        ) : (
          jobs.map((j) => (
            <div key={j.job_id}
              className={cn('bg-surface-container border rounded-cw-md px-5 py-4 mb-2.5 transition-colors duration-short3 group',
                j.status === 'failed' ? 'border-error/30' : 'border-outline-variant/30 hover:border-outline/60')}>
              <div className="flex items-center gap-3">
                <span className={cn('w-9 h-9 rounded-cw-sm flex items-center justify-center shrink-0',
                  j.status === 'failed' ? 'bg-error/15 text-error' : 'bg-primary/15 text-primary')}>
                  <GraduationCap className="w-4.5 h-4.5" />
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-body-sm font-semibold text-on-surface flex items-center gap-2 flex-wrap">
                    {j.name}
                    <Badge variant={jobStatusVariant(j.status)}>{jobStatusLabel(j.status)}</Badge>
                  </p>
                  <p className="font-mono text-caption text-on-surface-variant mt-0.5 truncate">
                    {j.job_id} · {j.base_model || '无基座模型'}
                    {j.total_epochs > 0 && ` · Epoch ${j.current_epoch}/${j.total_epochs}`}
                  </p>
                  {j.error && (
                    <p className="font-mono text-caption text-error mt-1 truncate">{j.error}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {TERMINAL_STATUSES.includes(j.status) ? (
                    <Button size="sm" variant="outline" onClick={() => startJob(j.job_id)} disabled={busyId === `start_${j.job_id}`}>
                      <Play className="w-3.5 h-3.5" /> {busyId === `start_${j.job_id}` ? '启动中' : '启动'}
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => cancelJob(j.job_id)} disabled={busyId === `cancel_${j.job_id}`}>
                      <Square className="w-3.5 h-3.5" /> {busyId === `cancel_${j.job_id}` ? '取消中' : '取消'}
                    </Button>
                  )}
                  <button onClick={() => deleteJob(j.job_id)} disabled={busyId === `del_${j.job_id}`} title="删除记录"
                    className="p-2 rounded-cw-xs text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors cursor-pointer disabled:opacity-50">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
              {isProgressing(j.status) && (
                <div className="mt-3">
                  <div className="h-1.5 bg-surface rounded-cw-full overflow-hidden">
                    <div className="h-full bg-primary transition-all duration-500" style={{ width: `${clampProgress(j.progress)}%` }} />
                  </div>
                  <p className="font-mono text-caption text-on-surface-variant mt-1">{clampProgress(j.progress).toFixed(1)}%</p>
                </div>
              )}
            </div>
          ))
        )}
      </section>

      {/* trained models */}
      <section className="max-w-[960px]">
        <h3 className="flex items-center gap-2 text-title-sm font-semibold text-on-surface mb-4">
          <Boxes className="w-4 h-4 text-primary" /> 已训练模型
          <span className="font-mono text-caption text-on-surface-variant font-normal">{models.length}</span>
        </h3>
        {modelsLoading ? (
          Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-14 bg-surface-container rounded-cw-md animate-pulse mb-2.5" />
          ))
        ) : models.length === 0 ? (
          <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-8 text-center">
            <Boxes className="w-7 h-7 text-on-surface-variant/40 mx-auto mb-2" />
            <p className="text-body-sm text-on-surface-variant">暂无已训练模型</p>
          </div>
        ) : (
          models.map((m, i) => {
            const id = modelField(m, 'model_id', 'id', `model_${i}`);
            const name = modelField(m, 'name', id);
            const base = modelField(m, 'base_model', 'base');
            const created = modelField(m, 'created_at', '');
            return (
              <div key={id}
                className="flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-3.5 mb-2.5
                  hover:border-outline/60 transition-colors duration-short3 group">
                <span className="w-9 h-9 rounded-cw-sm bg-track-audio/15 text-track-audio flex items-center justify-center shrink-0">
                  <Boxes className="w-4.5 h-4.5" />
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-body-sm font-semibold text-on-surface truncate">{name}</p>
                  <p className="font-mono text-caption text-on-surface-variant mt-0.5 truncate">
                    {id}{base && ` · ${base}`}{created && ` · ${created}`}
                  </p>
                </div>
              </div>
            );
          })
        )}
      </section>
    </ConsoleShell>
  );
}

function isProgressing(s: TrainingJob['status']): boolean {
  return s === 'preparing' || s === 'training' || s === 'evaluating';
}

function jobStatusLabel(s: TrainingJob['status']): string {
  return ({
    pending: '排队中',
    preparing: '准备中',
    training: '训练中',
    evaluating: '评估中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  } as Record<TrainingJob['status'], string>)[s] ?? s;
}

function jobStatusVariant(s: TrainingJob['status']): 'default' | 'success' | 'warning' | 'error' | 'info' {
  switch (s) {
    case 'completed': return 'success';
    case 'failed': return 'error';
    case 'pending': return 'warning';
    case 'evaluating': return 'warning';
    case 'preparing': return 'info';
    case 'training': return 'info';
    default: return 'default';
  }
}

function clampProgress(p: number): number {
  return Number.isFinite(p) ? Math.max(0, Math.min(100, p)) : 0;
}

function modelField(m: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = m[k];
    if (v != null && v !== '') return String(v);
  }
  return '';
}
