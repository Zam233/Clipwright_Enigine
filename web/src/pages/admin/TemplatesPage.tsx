import { useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { templateApi, projectApi } from '@/services/api';
import { Button, Badge } from '@/components/ui';
import { cn } from '@/lib/utils';
import { LayoutTemplate, Plus, Play, Trash2, Clock, Layers } from 'lucide-react';
import type { Timeline } from '@/types/timeline';

interface Template {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  trackCount: number;
  durationSec: number;
  updatedAt: string;
}

/**
 * TemplatesPage — manage reusable timeline templates. Backed by
 * /api/template/* (list/create/delete/apply). Applying a template creates a
 * new project from the template timeline and opens it in the editor.
 */
export function TemplatesPage() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState('');
  const [applyingId, setApplyingId] = useState<string | null>(null);

  const reload = async () => {
    try {
      const list = await templateApi.list();
      setTemplates(normalize(list));
      setNotice('');
    } catch {
      setNotice('无法连接后端模板服务');
    }
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      await reload();
      if (alive) setLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  const addNew = async () => {
    try {
      await templateApi.create({
        name: '新模板',
        description: '',
      });
      await reload();
    } catch {
      setNotice('新建失败：后端不可达');
    }
  };

  const remove = async (id: string) => {
    try {
      await templateApi.remove(id);
      setTemplates((ts) => ts.filter((t) => t.id !== id));
    } catch {
      setNotice('删除失败：后端不可达');
    }
  };

  const apply = async (t: Template) => {
    setApplyingId(t.id);
    setNotice('');
    try {
      const result = await templateApi.apply(t.id);
      const project = await projectApi.create({
        name: `${t.name} · 副本`,
        timeline: result.timeline as unknown as Timeline,
      });
      navigate({ to: '/editor/$projectId', params: { projectId: project.id } });
    } catch {
      setNotice('应用模板失败：后端不可达');
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Authoring / Templates" title="模板管理"
        desc="时间线模板封装了可复用的轨道结构与节奏。应用模板会基于它创建一个新项目并进入编辑器。" />

      {notice && (
        <div className="bg-error/10 border border-error/30 rounded-cw-sm px-3.5 py-2 mb-4 max-w-[900px]">
          <span className="font-mono text-caption text-error">{notice}</span>
        </div>
      )}

      <div className="flex items-center justify-between mb-5 max-w-[900px]">
        <p className="font-mono text-caption text-on-surface-variant">{templates.length} TEMPLATES</p>
        <Button size="sm" onClick={addNew}><Plus className="w-3.5 h-3.5" /> 新建模板</Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-[900px]">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-40 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : templates.length === 0 ? (
          <div className="col-span-2 bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-10 text-center">
            <LayoutTemplate className="w-7 h-7 text-on-surface-variant/40 mx-auto mb-2" />
            <p className="text-body-sm text-on-surface-variant">暂无模板</p>
          </div>
        ) : (
          templates.map((t) => (
            <div key={t.id}
              className="relative bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden
                hover:border-outline/60 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-black/20 transition-all duration-short3 group">
              <span className={cn('absolute top-0 left-0 w-full h-[3px] bg-gradient-to-r from-track-video to-transparent')} />

              <div className="px-4 pt-4 pb-3">
                <div className="flex items-start justify-between mb-2">
                  <span className="w-9 h-9 rounded-cw-sm flex items-center justify-center bg-track-video/15 text-track-video">
                    <LayoutTemplate className="w-4.5 h-4.5" />
                  </span>
                  <div className="flex items-center gap-1.5">
                    {t.category && <Badge variant="info">{t.category}</Badge>}
                    <button onClick={() => remove(t.id)}
                      className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-error hover:bg-error/10 opacity-0 group-hover:opacity-100 transition-all cursor-pointer">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <h3 className="text-body-sm font-semibold text-on-surface">{t.name}</h3>
                <p className="font-mono text-caption text-on-surface-variant mt-0.5">{t.id}</p>
              </div>

              <div className="px-4 pb-3">
                {t.description && (
                  <p className="text-caption text-on-surface-variant leading-relaxed mb-2">{t.description}</p>
                )}
                <div className="flex items-center gap-4 font-mono text-caption text-on-surface-variant">
                  <span className="flex items-center gap-1"><Layers className="w-3 h-3" /> {t.trackCount} 轨</span>
                  <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {t.durationSec.toFixed(1)}s</span>
                </div>
                {t.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {t.tags.map((tag) => (
                      <span key={tag} className="font-mono text-caption px-2 py-0.5 rounded-cw-xs bg-primary/10 text-primary border border-primary/30">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="px-4 pb-4">
                <Button size="sm" variant="outline" className="w-full" onClick={() => apply(t)} disabled={applyingId === t.id}>
                  <Play className="w-3.5 h-3.5" /> {applyingId === t.id ? '应用中…' : '应用为新项目'}
                </Button>
              </div>
            </div>
          ))
        )}
      </div>
    </ConsoleShell>
  );
}

function normalize(data: unknown): Template[] {
  if (Array.isArray(data)) {
    return data.map((d, i) => {
      const o = d as Record<string, unknown>;
      return {
        id: String(o.template_id ?? o.id ?? `tpl_${i}`),
        name: String(o.name ?? '模板'),
        description: String(o.description ?? ''),
        category: String(o.category ?? ''),
        tags: Array.isArray(o.tags) ? (o.tags as unknown[]).map(String) : [],
        trackCount: Number(o.track_count ?? 0),
        durationSec: Number(o.duration_sec ?? 0),
        updatedAt: String(o.updated_at ?? ''),
      };
    });
  }
  return [];
}
