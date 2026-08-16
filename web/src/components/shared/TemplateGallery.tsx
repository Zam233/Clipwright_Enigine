import { useEffect, useState } from 'react';
import { templateApi, projectApi } from '@/services/api';
import type { Timeline } from '@/types/timeline';
import { Button, Badge } from '@/components/ui';
import { LayoutTemplate, X, Play, Layers, Clock, Loader2 } from 'lucide-react';
import { toast } from '@/stores/toastStore';

interface TemplateCard {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  trackCount: number;
  durationSec: number;
}

/**
 * A3: 模板画廊 — HomePage「从模板开始」入口。列出后端模板，
 * 应用后创建新项目并进入编辑器。
 */
export function TemplateGallery({ open, onClose, onApplyProject }: {
  open: boolean;
  onClose: () => void;
  /** 模板应用成功创建项目后回调（默认跳转编辑器，可覆盖） */
  onApplyProject?: (projectId: string) => void;
}) {
  const [templates, setTemplates] = useState<TemplateCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [applyingId, setApplyingId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    templateApi.list()
      .then((list) => { if (alive) setTemplates(normalize(list)); })
      .catch(() => { if (alive) toast('模板加载失败（后端离线？）', 'error'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open]);

  if (!open) return null;

  const apply = async (t: TemplateCard) => {
    setApplyingId(t.id);
    try {
      const result = await templateApi.apply(t.id);
      const project = await projectApi.create({
        name: `${t.name} · 副本`,
        timeline: result.timeline as unknown as Timeline,
      });
      toast(`已基于「${t.name}」创建项目`, 'success');
      onClose();
      if (onApplyProject) onApplyProject(project.id);
    } catch {
      toast('应用模板失败：后端不可达', 'error');
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="relative w-[640px] max-w-[92vw] max-h-[82vh] bg-surface-container-high border border-outline-variant/50
          rounded-cw-lg shadow-2xl shadow-black/60 overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-4 border-b border-outline-variant/30 bg-surface-container">
          <span className="w-9 h-9 rounded-cw-sm bg-primary-container flex items-center justify-center">
            <LayoutTemplate className="w-4.5 h-4.5 text-on-primary-container" />
          </span>
          <div className="flex-1">
            <h2 className="text-title-sm font-bold text-on-surface">从模板开始</h2>
            <p className="font-mono text-caption text-on-surface-variant">TIMELINE TEMPLATES</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface transition-colors cursor-pointer" aria-label="关闭">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <Loader2 className="w-6 h-6 text-primary animate-spin" />
              <span className="text-caption text-on-surface-variant">加载模板中…</span>
            </div>
          ) : templates.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <LayoutTemplate className="w-10 h-10 text-on-surface-variant/40" />
              <p className="text-body-sm text-on-surface-variant">暂无模板 — 可在 设置 → 模板管理 中创建</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {templates.map((t) => (
                <div key={t.id}
                  className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden
                    hover:border-outline/60 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-black/20 transition-all duration-short3">
                  <div className="px-4 pt-4 pb-2 flex items-start justify-between">
                    <span className="w-9 h-9 rounded-cw-sm flex items-center justify-center bg-track-video/15 text-track-video">
                      <LayoutTemplate className="w-4.5 h-4.5" />
                    </span>
                    {t.category && <Badge variant="info">{t.category}</Badge>}
                  </div>
                  <div className="px-4">
                    <h3 className="text-body-sm font-semibold text-on-surface">{t.name}</h3>
                    {t.description && (
                      <p className="text-caption text-on-surface-variant leading-relaxed mt-1 line-clamp-2">{t.description}</p>
                    )}
                    <div className="flex items-center gap-4 font-mono text-caption text-on-surface-variant mt-2">
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
                  <div className="px-4 py-3">
                    <Button size="sm" variant="outline" className="w-full" onClick={() => apply(t)} disabled={applyingId === t.id}>
                      <Play className="w-3.5 h-3.5" /> {applyingId === t.id ? '应用中…' : '应用为新项目'}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function normalize(data: unknown): TemplateCard[] {
  if (!Array.isArray(data)) return [];
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
    };
  });
}
