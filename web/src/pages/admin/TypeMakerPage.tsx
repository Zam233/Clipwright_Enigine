import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { typeMakerApi } from '@/services/api';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Plus, Copy, Trash2, Pencil, Film } from 'lucide-react';

interface VideoType {
  id: string;
  name: string;
  shot_duration: string;      // e.g. "5-15s"
  transition: string;
  animation_density: string;
  cut_interval_ms: number;
  color: string;
  builtin: boolean;
}

/**
 * TypeMakerPage — author video-type plugins. Each type is a card showing its
 * rhythmic signature (shot length / transition / animation density).
 * CRUD backed by /api/type-maker/* (built-in types are read-only).
 */
export function TypeMakerPage() {
  const [types, setTypes] = useState<VideoType[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [notice, setNotice] = useState('');

  const reload = async () => {
    try {
      const list = await typeMakerApi.list();
      setTypes(normalize(list));
      setNotice('');
    } catch {
      setNotice('无法连接后端类型服务');
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

  const duplicate = async (t: VideoType) => {
    try {
      const definition = await typeMakerApi.get(t.id) as unknown as Record<string, unknown>;
      const newId = `${t.id}_copy_${Date.now().toString(36).slice(-4)}`;
      await typeMakerApi.create({
        ...definition,
        id: newId,
        name: `${t.name} 副本`,
      } as never);
      await reload();
    } catch {
      setNotice('复制失败：后端不可达或 ID 冲突');
    }
  };

  const remove = async (id: string) => {
    try {
      await typeMakerApi.remove(id);
      setTypes((ts) => ts.filter((t) => t.id !== id));
    } catch {
      setNotice('删除失败：后端不可达或内置类型受保护');
    }
  };

  const addNew = async () => {
    const id = `custom_${Date.now().toString(36)}`;
    try {
      await typeMakerApi.create({
        id,
        name: '新视频类型',
        description: '',
        shot_params: { min_shot_sec: 1, max_shot_sec: 3, transition_type: 'cut', transition_duration_sec: 0.5, cut_on_beat: false },
      } as never);
      await reload();
      setEditing(id);
    } catch {
      setNotice('新建失败：后端不可达');
    }
  };

  const commitEdit = async (t: VideoType) => {
    setEditing(null);
    try {
      const definition = await typeMakerApi.get(t.id) as unknown as Record<string, unknown>;
      const maxShot = Math.max(0.5, t.cut_interval_ms / 1000);
      const prevShot = (definition.shot_params ?? {}) as Record<string, unknown>;
      await typeMakerApi.update(t.id, {
        ...definition,
        id: t.id,
        name: t.name,
        shot_params: {
          ...prevShot,
          min_shot_sec: Math.max(0.3, maxShot / 3),
          max_shot_sec: maxShot,
        },
      } as never);
      await reload();
    } catch {
      setNotice('保存失败：后端不可达');
    }
  };

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Authoring / Type Maker" title="类型制作器"
        desc="视频类型封装了品类级的剪辑逻辑差异——同一个 Persona 套用不同类型，产出风格一致但节奏迥异的视频。" />

      {notice && (
        <div className="bg-error/10 border border-error/30 rounded-cw-sm px-3.5 py-2 mb-4 max-w-[900px]">
          <span className="font-mono text-caption text-error">{notice}</span>
        </div>
      )}

      <div className="flex items-center justify-between mb-5 max-w-[900px]">
        <p className="font-mono text-caption text-on-surface-variant">{types.length} TYPES DEFINED</p>
        <Button size="sm" onClick={addNew}><Plus className="w-3.5 h-3.5" /> 新建类型</Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-[900px]">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-44 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : (
          types.map((t) => (
            <TypeCard key={t.id} type={t} editing={editing === t.id}
              onEdit={() => (editing === t.id ? commitEdit(t) : setEditing(t.id))}
              onDuplicate={() => duplicate(t)}
              onRemove={() => remove(t.id)}
              onChange={(patch) => setTypes((ts) => ts.map((x) => (x.id === t.id ? { ...x, ...patch } : x)))} />
          ))
        )}
      </div>
    </ConsoleShell>
  );
}

function TypeCard({
  type, editing, onEdit, onDuplicate, onRemove, onChange,
}: {
  type: VideoType; editing: boolean; onEdit: () => void; onDuplicate: () => void;
  onRemove: () => void; onChange: (patch: Partial<VideoType>) => void;
}) {
  // rhythm bars derived from cut interval (lower interval = denser)
  const density = Math.max(1, Math.min(8, Math.round(8 - type.cut_interval_ms / 2000)));
  const bars = Array.from({ length: 10 }, (_, i) => ((i * 7 + density * 3) % 9) + 1);

  return (
    <div className="relative bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden
      hover:border-outline/60 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-black/20 transition-all duration-short3 group">
      <span className="absolute top-0 left-0 w-full h-[3px]" style={{ background: `linear-gradient(90deg, ${type.color}, transparent)` }} />

      <div className="px-4 pt-4 pb-3">
        <div className="flex items-start justify-between mb-2">
          <span className="w-9 h-9 rounded-cw-sm flex items-center justify-center" style={{ background: `${type.color}1A`, color: type.color }}>
            <Film className="w-4.5 h-4.5" />
          </span>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-short3">
            <IconBtn title="编辑" onClick={onEdit}><Pencil className="w-3.5 h-3.5" /></IconBtn>
            <IconBtn title="复制" onClick={onDuplicate}><Copy className="w-3.5 h-3.5" /></IconBtn>
            {!type.builtin && (
              <IconBtn title="删除" onClick={onRemove} danger><Trash2 className="w-3.5 h-3.5" /></IconBtn>
            )}
          </div>
        </div>

        {editing ? (
          <input value={type.name} onChange={(e) => onChange({ name: e.target.value })}
            className="w-full bg-surface rounded-cw-xs px-2 py-1 text-body-sm font-semibold text-on-surface outline-none border border-primary" />
        ) : (
          <h3 className="text-body-sm font-semibold text-on-surface">{type.name}</h3>
        )}
        <p className="font-mono text-caption text-on-surface-variant mt-0.5">{type.id}</p>
      </div>

      {/* rhythm signature */}
      <div className="px-4 pb-3">
        <div className="flex items-end gap-1 h-9 mb-2">
          {bars.map((b, i) => (
            <span key={i} className="flex-1 rounded-t-[2px] opacity-75 transition-all duration-short3 group-hover:opacity-100"
              style={{ height: `${(b / 9) * 100}%`, background: type.color }} />
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <Spec label="镜头时长" value={type.shot_duration} />
          <Spec label="转场" value={type.transition} />
          <Spec label="动画密度" value={type.animation_density} />
        </div>
      </div>

      {editing && (
        <div className="px-4 pb-4 space-y-2 border-t border-outline-variant/20 pt-3">
          <div className="flex items-center gap-2">
            <span className="text-label text-on-surface-variant w-20 shrink-0">剪切间隔</span>
            <input type="range" min={300} max={15000} step={100} value={type.cut_interval_ms}
              onChange={(e) => onChange({ cut_interval_ms: Number(e.target.value) })}
              className="flex-1 accent-primary cursor-pointer" />
            <span className="font-mono text-caption text-primary w-14 text-right">{type.cut_interval_ms}ms</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Spec({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface rounded-cw-xs py-1.5 px-1">
      <p className="text-caption text-on-surface-variant">{label}</p>
      <p className="text-label-sm font-mono text-on-surface mt-0.5">{value}</p>
    </div>
  );
}

function IconBtn({ children, title, onClick, danger }: { children: React.ReactNode; title: string; onClick: () => void; danger?: boolean }) {
  return (
    <button title={title} onClick={onClick}
      className={cn('p-1.5 rounded-cw-xs transition-colors cursor-pointer',
        danger ? 'text-on-surface-variant hover:text-error hover:bg-error/10' : 'text-on-surface-variant hover:text-primary hover:bg-primary/10')}>
      {children}
    </button>
  );
}

function normalize(data: unknown): VideoType[] {
  if (Array.isArray(data)) {
    return data.map((d, i) => {
      const o = d as Record<string, unknown>;
      return {
        id: String(o.id ?? `type_${i}`),
        name: String(o.name ?? o.id ?? '类型'),
        shot_duration: String(o.shot_duration ?? '3-8s'),
        transition: String(o.transition ?? '硬切'),
        animation_density: String(o.animation_density ?? '中'),
        cut_interval_ms: Number(o.cut_interval_ms ?? 3000),
        color: ['#4F8CFF', '#FF6B6B', '#A855F7', '#34D399'][i % 4],
        builtin: Boolean(o.builtin),
      };
    });
  }
  return [];
}
