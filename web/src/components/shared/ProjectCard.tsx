import { memo, useEffect, useRef, useState } from 'react';
import { Layers, Clock, X, MoreVertical, FolderOpen, Check, Trash2 } from 'lucide-react';

/* ── shared project card type ──────────────────────────── */
export interface ProjectCardData {
  id: string;
  name: string;
  type: string;
  duration: string;
  tracks: number;
  edited: string;
  grad: [string, string];
  featured?: boolean;
  folder?: string;
  tags?: string[];
  thumbnail?: string;
}

/* ── shared ProjectCard ────────────────────────────────── */
export const ProjectCard = memo(function ProjectCard({
  proj,
  onOpen,
  onDelete,
  onRename,
  onSetFolder,
  onAddTag,
  onRemoveTag,
  onDuplicate,
  onRefreshThumbnail,
  onArchive,
  folders,
  mode = 'full',
}: {
  proj: ProjectCardData;
  onOpen: () => void;
  onDelete: () => void;
  onRename?: (name: string) => void;
  onSetFolder?: (folder: string) => void;
  onAddTag?: (tag: string) => void;
  onRemoveTag?: (tag: string) => void;
  onDuplicate?: () => void;
  onRefreshThumbnail?: () => void;
  /** P8: 导出项目归档 zip */
  onArchive?: () => void;
  folders?: string[];
  mode?: 'simple' | 'full';
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState(proj.name);
  const [imgFailed, setImgFailed] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const span = proj.featured ? 'col-span-12 md:col-span-6' : 'col-span-12 sm:col-span-6 md:col-span-3';

  /* close menu on outside click / Escape */
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const keyHandler = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', handler, true);
    document.addEventListener('keydown', keyHandler);
    return () => { document.removeEventListener('mousedown', handler, true); document.removeEventListener('keydown', keyHandler); };
  }, [menuOpen]);

  const commitRename = () => {
    const trimmed = renameDraft.trim();
    if (trimmed && trimmed !== proj.name) onRename?.(trimmed);
    setRenaming(false);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.currentTarget === event.target && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          onOpen();
        }
      }}
      className={`${span} relative text-left group bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden
        hover:border-primary/60 hover:-translate-y-1 hover:shadow-xl hover:shadow-primary/10
        transition-all duration-medium2 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary`}
    >
      {/* delete button (X) — always present */}
      {confirmDelete ? (
        <div className="absolute top-2 right-2 z-10 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <button
            type="button" onClick={() => { void onDelete(); setConfirmDelete(false); }}
            className="px-2 py-1 rounded-cw-xs bg-error text-on-error text-caption font-medium cursor-pointer hover:bg-error/90"
          >
            <Trash2 className="w-3 h-3" />
          </button>
          <button
            type="button" onClick={() => setConfirmDelete(false)}
            className="px-2 py-1 rounded-cw-xs bg-black/50 text-white/70 text-caption cursor-pointer hover:bg-black/70"
          >
            取消
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); setConfirmDelete(true); }}
          className="absolute top-2 right-2 z-10 p-1.5 rounded-cw-sm bg-black/50 text-white/70 opacity-0
            group-hover:opacity-100 focus:opacity-100 hover:text-error hover:bg-black/70 transition-all cursor-pointer"
          title="删除项目"
          aria-label={`删除项目 ${proj.name}`}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      )}

      {/* kebab menu button — full mode only */}
      {mode === 'full' && (
        <div ref={menuRef} className="absolute top-2 left-2 z-20">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setMenuOpen((o) => !o); }}
            className="p-1.5 rounded-cw-sm bg-black/50 text-white/70 opacity-0
              group-hover:opacity-100 focus:opacity-100 hover:bg-black/70 transition-all cursor-pointer"
            title="更多操作"
            aria-label="更多操作"
          >
            <MoreVertical className="w-3.5 h-3.5" />
          </button>

          {/* dropdown */}
          {menuOpen && (
            <div
              className="absolute left-0 top-full mt-1 w-44 bg-surface-container-high border border-outline-variant/40
                rounded-cw-sm shadow-xl overflow-hidden text-caption text-on-surface"
              onClick={(e) => e.stopPropagation()}
            >
              <MenuItem label="重命名" onClick={() => { setRenaming(true); setRenameDraft(proj.name); setMenuOpen(false); }} />
              {folders && folders.length > 0 && (
                <MenuItem label="移动到文件夹" icon={<FolderOpen className="w-3 h-3" />}>
                  {folders.map((f) => (
                    <button key={f} onClick={() => { onSetFolder?.(f); setMenuOpen(false); }}
                      className={`block w-full text-left px-3 py-1.5 text-caption hover:bg-primary/10 transition-colors cursor-pointer ${f === proj.folder ? 'text-primary font-medium' : ''}`}>
                      {f}
                    </button>
                  ))}
                  <button onClick={() => {
                    const name = window.prompt('新建文件夹名称');
                    if (name?.trim()) { onSetFolder?.(name.trim()); }
                    setMenuOpen(false);
                  }}
                    className="block w-full text-left px-3 py-1.5 text-caption text-primary hover:bg-primary/10 transition-colors cursor-pointer border-t border-outline-variant/20">
                    新建文件夹…
                  </button>
                </MenuItem>
              )}
              <MenuItem label="添加标签" onClick={() => {
                const tag = window.prompt('输入标签名称');
                if (tag?.trim()) onAddTag?.(tag.trim());
                setMenuOpen(false);
              }} />
              <MenuItem label="重新生成封面" onClick={() => { onRefreshThumbnail?.(); setMenuOpen(false); }} />
              <MenuItem label="创建副本" onClick={() => { onDuplicate?.(); setMenuOpen(false); }} />
              {onArchive && (
                <MenuItem label="导出归档" onClick={() => { onArchive(); setMenuOpen(false); }} />
              )}
              <div className="border-t border-outline-variant/20" />
              <MenuItem label="删除" danger onClick={() => { onDelete(); setMenuOpen(false); }} />
            </div>
          )}
        </div>
      )}

      {/* thumbnail area */}
      <div
        className={`relative ${proj.featured ? 'h-36' : 'h-24'} overflow-hidden`}
        style={{ background: (proj.thumbnail && !imgFailed)
          ? undefined
          : `linear-gradient(120deg, ${proj.grad[0]}33, ${proj.grad[1]}22)` }}
      >
        {proj.thumbnail && !imgFailed && (
          <img src={proj.thumbnail} alt={proj.name}
            onError={() => setImgFailed(true)}
            className="absolute inset-0 w-full h-full object-cover" />
        )}
        <div className="absolute top-1.5 left-0 right-0 flex gap-2 px-2 opacity-40">
          {Array.from({ length: 14 }).map((_, i) => (
            <i key={i} className="w-3 h-2 rounded-[2px] bg-black/50 shrink-0" />
          ))}
        </div>
        <div className="absolute bottom-1.5 left-0 right-0 flex gap-2 px-2 opacity-40">
          {Array.from({ length: 14 }).map((_, i) => (
            <i key={i} className="w-3 h-2 rounded-[2px] bg-black/50 shrink-0" />
          ))}
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <span
            className="w-11 h-11 rounded-full flex items-center justify-center bg-black/40 border border-white/20
              group-hover:scale-110 group-hover:bg-primary/80 transition-all duration-short3"
          >
            <svg className="w-4 h-4 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
          </span>
        </div>
        <span className="absolute bottom-3 right-2 font-mono text-caption bg-black/60 text-white px-1.5 py-0.5 rounded-cw-xs">
          {proj.duration}
        </span>
      </div>

      {/* info area */}
      <div className="p-3.5">
        {renaming ? (
          <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <input autoFocus value={renameDraft}
              onChange={(e) => setRenameDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenaming(false); }}
              onBlur={commitRename}
              className="flex-1 min-w-0 px-1.5 py-0.5 text-body-sm font-semibold bg-surface border border-primary rounded-cw-xs
                text-on-surface outline-none focus:ring-1 focus:ring-primary"
            />
            <button type="button" onClick={commitRename}
              className="p-0.5 text-primary hover:text-on-primary hover:bg-primary rounded-cw-xs transition-colors cursor-pointer">
              <Check className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <p className="text-body-sm font-semibold text-on-surface truncate group-hover:text-primary transition-colors">
            {proj.name}
          </p>
        )}
        <div className="flex items-center gap-3 mt-1.5 text-caption text-on-surface-variant">
          <span className="truncate">{proj.type}</span>
          <span className="flex items-center gap-1 shrink-0"><Layers className="w-3 h-3" />{proj.tracks} 轨</span>
          <span className="flex items-center gap-1 ml-auto shrink-0"><Clock className="w-3 h-3" />{proj.edited}</span>
        </div>
        {proj.tags && proj.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {proj.tags.map((tag) => (
              <span key={tag} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-cw-full text-caption bg-primary/10 text-primary border border-primary/20">
                {tag}
                {onRemoveTag && (
                  <button onClick={(e) => { e.stopPropagation(); onRemoveTag(tag); }}
                    className="ml-0.5 hover:text-error cursor-pointer">
                    <X className="w-2.5 h-2.5" />
                  </button>
                )}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

/* ── menu helpers ──────────────────────────────────────── */
function MenuItem({ label, icon, danger, onClick, children }: {
  label: string;
  icon?: React.ReactNode;
  danger?: boolean;
  onClick?: () => void;
  children?: React.ReactNode;
}) {
  if (children) {
    return (
      <div className="group/nested">
        <div className={`flex items-center gap-2 px-3 py-1.5 text-caption ${danger ? 'text-error' : 'text-on-surface'} hover:bg-surface-container transition-colors cursor-pointer`}>
          {icon}
          <span className="flex-1">{label}</span>
          <span className="text-on-surface-variant/50 text-[10px]">▸</span>
        </div>
        <div className="hidden group-hover/nested:block bg-surface-container-high border border-outline-variant/20 rounded-cw-xs shadow-lg ml-2">
          {children}
        </div>
      </div>
    );
  }
  return (
    <button type="button" onClick={onClick}
      className={`flex items-center gap-2 w-full px-3 py-1.5 text-caption text-left ${danger ? 'text-error' : 'text-on-surface'} hover:bg-surface-container transition-colors cursor-pointer`}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
