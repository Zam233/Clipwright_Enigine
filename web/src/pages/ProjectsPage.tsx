import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { projectApi } from '@/services/api';
import { ProjectCard, type ProjectCardData } from '@/components/shared/ProjectCard';
import { Badge } from '@/components/ui';
import {
  Film, ArrowLeft, Search, FolderOpen, Folder, X, Plus,
  Loader2, PackageOpen, Tag, Trash2, RotateCcw, ChevronLeft,
} from 'lucide-react';
import { fmtDur, relTime } from '@/lib/utils';
import { toast } from '@/stores/toastStore';

const GRADIENTS: [string, string][] = [
  ['#A855F7', '#4F8CFF'], ['#4F8CFF', '#34D399'], ['#FF6B6B', '#FBBF24'], ['#34D399', '#F59E0B'],
];

/* ── page ──────────────────────────────────────────────── */
export function ProjectsPage() {
  const navigate = useNavigate();

  const [projects, setProjects] = useState<ProjectCardData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedFolder, setSelectedFolder] = useState<string | null>(null); // null=全部, ''=未分组, else name
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'updated' | 'name'>('updated'); // A1

  const [newFolderPrompt, setNewFolderPrompt] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  // A2: 回收站视图
  const [showTrash, setShowTrash] = useState(false);
  const [trashProjects, setTrashProjects] = useState<ProjectCardData[]>([]);

  /* ── load projects (reusable) ─────────────────────── */
  const loadProjects = async () => {
    try {
      const data = await projectApi.list();
      setProjects(
        data.map((pr, i) => ({
          id: pr.id,
          name: pr.name,
          type: pr.plugin_id ?? '—',
          duration: fmtDur(pr.duration_sec ?? 0),
          tracks: pr.track_count ?? 0,
          edited: relTime(pr.updated_at),
          grad: GRADIENTS[i % GRADIENTS.length],
          featured: i === 0,
          folder: pr.folder,
          tags: pr.tags,
          thumbnail: pr.has_thumbnail ? projectApi.getThumbnailUrl(pr.id, pr.updated_at) : undefined,
        })),
      );
    } catch {
      setError('后端未连接，无法加载项目');
    }
  };

  /* A2: 加载回收站项目 */
  const loadTrash = async () => {
    try {
      const data = await projectApi.list(undefined, undefined, true);
      setTrashProjects(
        data.map((pr, i) => ({
          id: pr.id,
          name: pr.name,
          type: pr.plugin_id ?? '—',
          duration: fmtDur(pr.duration_sec ?? 0),
          tracks: pr.track_count ?? 0,
          edited: relTime(pr.updated_at),
          grad: GRADIENTS[i % GRADIENTS.length],
          featured: i === 0,
          folder: pr.folder,
          tags: pr.tags,
          thumbnail: pr.has_thumbnail ? projectApi.getThumbnailUrl(pr.id, pr.updated_at) : undefined,
        })),
      );
    } catch {
      setError('后端未连接，无法加载回收站');
    }
  };

  useEffect(() => {
    let alive = true;
    loadProjects()
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  /* ── derived ───────────────────────────────────────── */
  const folders = useMemo(() => {
    const set = new Set<string>();
    projects.forEach((p) => { if (p.folder) set.add(p.folder); });
    return Array.from(set).sort();
  }, [projects]);

  const allTags = useMemo(() => {
    const set = new Set<string>();
    projects.forEach((p) => p.tags?.forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [projects]);

  const filtered = useMemo(() => {
    let list = projects;
    if (selectedFolder !== null) {
      list = list.filter((p) => (p.folder || '') === selectedFolder);
    }
    if (selectedTag) {
      list = list.filter((p) => p.tags?.includes(selectedTag));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q) || p.type.toLowerCase().includes(q));
    }
    // A1: 排序（最近编辑默认 / 名称）
    const arr = [...list];
    if (sortBy === 'name') {
      arr.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    } else {
      arr.sort((a, b) => String(b.edited ?? '').localeCompare(String(a.edited ?? '')));
    }
    return arr;
  }, [projects, selectedFolder, selectedTag, search, sortBy]);

  /* ── actions ───────────────────────────────────────── */
  const handleOpen = (proj: ProjectCardData) => {
    navigate({ to: '/editor/$projectId', params: { projectId: proj.id } });
  };

  // A2: 删除 = 移入回收站（软删除，可恢复）
  const handleDelete = async (proj: ProjectCardData) => {
    try {
      await projectApi.trashProject(proj.id);
      setProjects((prev) => prev.filter((p) => p.id !== proj.id));
      toast(`「${proj.name}」已移入回收站`, 'success');
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      toast(`删除失败：${reason}，项目已保留`, 'error');
    }
  };

  const handleRestore = async (proj: ProjectCardData) => {
    try {
      await projectApi.restoreProject(proj.id);
      setTrashProjects((prev) => prev.filter((p) => p.id !== proj.id));
      toast(`「${proj.name}」已恢复`, 'success');
      await loadProjects();
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      toast(`恢复失败：${reason}`, 'error');
    }
  };

  const handlePurge = async (proj: ProjectCardData) => {
    if (!window.confirm(`永久删除「${proj.name}」？此操作不可恢复。`)) return;
    try {
      await projectApi.purgeProject(proj.id);
      setTrashProjects((prev) => prev.filter((p) => p.id !== proj.id));
      toast(`「${proj.name}」已永久删除`, 'success');
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      toast(`永久删除失败：${reason}`, 'error');
    }
  };

  const handleToggleTrash = () => {
    setShowTrash((prev) => {
      const next = !prev;
      if (next) loadTrash();
      return next;
    });
  };

  const handleRename = async (proj: ProjectCardData, name: string) => {
    try {
      const updated = await projectApi.rename(proj.id, name);
      setProjects((prev) => prev.map((p) => (p.id === proj.id ? { ...p, name: updated.name } : p)));
    } catch { toast('重命名失败', 'error'); }
  };

  const handleMoveFolder = async (proj: ProjectCardData, folder: string) => {
    try {
      await projectApi.setFolder(proj.id, folder);
      setProjects((prev) => prev.map((p) => (p.id === proj.id ? { ...p, folder } : p)));
    } catch { toast('移动文件夹失败', 'error'); }
  };

  const handleAddTag = async (proj: ProjectCardData, tag: string) => {
    try {
      await projectApi.addTag(proj.id, tag);
      setProjects((prev) => prev.map((p) => (p.id === proj.id ? { ...p, tags: [...(p.tags || []), tag] } : p)));
    } catch { toast('添加标签失败', 'error'); }
  };

  const handleRemoveTag = async (proj: ProjectCardData, tag: string) => {
    try {
      await projectApi.removeTag(proj.id, tag);
      setProjects((prev) => prev.map((p) => (p.id === proj.id ? { ...p, tags: (p.tags || []).filter((t) => t !== tag) } : p)));
    } catch { toast('移除标签失败', 'error'); }
  };

  const handleDuplicate = async (proj: ProjectCardData) => {
    try {
      await projectApi.duplicate(proj.id);
      await loadProjects();
    } catch { toast('复制项目失败', 'error'); }
  };

  // P8: 导出项目归档 zip
  const handleArchive = async (proj: ProjectCardData) => {
    try {
      await projectApi.archive(proj.id, proj.name);
      toast(`已导出归档「${proj.name}」`, 'success');
    } catch {
      toast('导出归档失败（后端离线）', 'error');
    }
  };

  const handleRefreshThumbnail = (proj: ProjectCardData) => {
    setProjects((prev) => prev.map((p) =>
      p.id === proj.id ? { ...p, thumbnail: projectApi.refreshThumbnailUrl(p.id) } : p,
    ));
  };

  const handleRenameFolder = async (oldName: string) => {
    const newName = window.prompt('重命名文件夹', oldName);
    if (newName && newName.trim() && newName.trim() !== oldName) {
      try {
        await projectApi.renameFolder(oldName, newName.trim());
        await loadProjects();
      } catch { toast('重命名文件夹失败', 'error'); }
    }
  };

  const handleDeleteFolder = async (folderName: string) => {
    if (window.confirm(`删除文件夹「${folderName}」？项目不会被删除，仅取消分组。`)) {
      try {
        await projectApi.deleteFolder(folderName);
        await loadProjects();
        if (selectedFolder === folderName) setSelectedFolder(null);
      } catch { toast('删除文件夹失败', 'error'); }
    }
  };

  const handleCreateFolder = () => {
    const name = newFolderName.trim();
    if (name) {
      setSelectedFolder(name);
      setNewFolderName('');
      setNewFolderPrompt(false);
    }
  };

  /* ── render ────────────────────────────────────────── */
  return (
    <div className="min-h-full h-full overflow-y-auto bg-surface text-on-surface">
      {/* Header */}
      <header className="flex items-center gap-3 px-8 py-4 max-w-[1200px] w-full mx-auto">
        <button onClick={() => navigate({ to: '/' })}
          className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors cursor-pointer">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="w-9 h-9 rounded-cw-sm bg-primary-container flex items-center justify-center shadow-lg shadow-primary/20">
          <Film className="w-5 h-5 text-on-primary-container" />
        </div>
        <div className="leading-tight">
          <p className="text-title-sm font-bold text-on-surface tracking-wide">我的项目</p>
          <p className="font-mono text-caption text-on-surface-variant tracking-[0.2em]">MY PROJECTS</p>
        </div>
        <Badge variant="default" className="ml-2">{filtered.length} 个项目</Badge>
        {/* A2: 回收站切换 */}
        <button onClick={handleToggleTrash}
          className={`ml-3 flex items-center gap-1.5 px-3 py-1.5 rounded-cw-sm text-label-sm transition-colors cursor-pointer ${
            showTrash
              ? 'bg-primary/10 text-primary'
              : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container'
          }`}>
          {showTrash ? <ChevronLeft className="w-3.5 h-3.5" /> : <Trash2 className="w-3.5 h-3.5" />}
          {showTrash ? '返回项目' : '回收站'}
        </button>
        <button onClick={() => navigate({ to: '/' })}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-cw-sm text-label-sm text-on-surface-variant
            hover:text-on-surface hover:bg-surface-container transition-colors cursor-pointer">
          返回主页
        </button>
      </header>

      <div className="max-w-[1200px] mx-auto px-8 pb-10 flex gap-6">
        {/* ── folder sidebar ── */}
        <aside className="w-48 shrink-0 hidden md:block">
          <p className="font-mono text-label-sm tracking-[0.2em] text-on-surface-variant uppercase mb-3">文件夹</p>
          <nav className="flex flex-col gap-0.5">
            <FolderSidebarItem label="全部" active={selectedFolder === null}
              onClick={() => setSelectedFolder(null)} count={projects.length} />
            <FolderSidebarItem label="未分组" active={selectedFolder === ''}
              onClick={() => setSelectedFolder('')} count={projects.filter((p) => !p.folder).length} />
            {folders.map((f) => (
              <FolderSidebarItem key={f} label={f} active={selectedFolder === f}
                onClick={() => setSelectedFolder(f)}
                count={projects.filter((p) => p.folder === f).length}
                onRename={() => handleRenameFolder(f)}
                onDelete={() => handleDeleteFolder(f)} />
            ))}
          </nav>
          <div className="mt-3">
            {newFolderPrompt ? (
              <div className="flex gap-1">
                <input value={newFolderName} onChange={(e) => setNewFolderName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleCreateFolder(); if (e.key === 'Escape') setNewFolderPrompt(false); }}
                  autoFocus placeholder="文件夹名…"
                  className="flex-1 bg-surface border border-outline-variant/40 rounded-cw-xs px-2 py-1 text-caption text-on-surface outline-none" />
                <button onClick={handleCreateFolder}
                  className="p-1 rounded-cw-xs text-primary hover:bg-primary/10 cursor-pointer"><Plus className="w-3.5 h-3.5" /></button>
              </div>
            ) : (
              <button onClick={() => setNewFolderPrompt(true)}
                className="flex items-center gap-1.5 text-caption text-on-surface-variant hover:text-primary transition-colors cursor-pointer">
                <Plus className="w-3 h-3" /> 新建文件夹
              </button>
            )}
          </div>
        </aside>

        {/* ── main area ── */}
        <main className="flex-1 min-w-0">
          {/* tag chips + search */}
          <div className="flex flex-wrap items-center gap-2 mb-4">
            {allTags.map((t) => (
              <button key={t} onClick={() => setSelectedTag(selectedTag === t ? null : t)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-cw-full text-caption border transition-all cursor-pointer ${
                  selectedTag === t
                    ? 'bg-primary text-on-primary border-primary'
                    : 'bg-surface-container text-on-surface-variant border-outline-variant/30 hover:border-primary/50'
                }`}>
                <Tag className="w-3 h-3" /> {t}
                {selectedTag === t && <X className="w-3 h-3 ml-0.5" />}
              </button>
            ))}
            <div className="ml-auto relative">
              <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-on-surface-variant/50" />
              <input value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索项目…"
                className="bg-surface-container border border-outline-variant/30 rounded-cw-sm pl-7 pr-8 py-1.5 text-caption text-on-surface outline-none
                  focus:border-primary/60 transition-colors w-48" />
              {search && (
                <button onClick={() => setSearch('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 text-on-surface-variant/50 hover:text-on-surface cursor-pointer">
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
            {/* A1: 排序 */}
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as 'updated' | 'name')}
              className="bg-surface-container border border-outline-variant/30 rounded-cw-sm px-2 py-1.5 text-caption text-on-surface outline-none cursor-pointer"
              aria-label="排序方式"
            >
              <option value="updated">最近编辑</option>
              <option value="name">名称</option>
            </select>
          </div>

          {/* project grid */}
          {showTrash ? (
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Trash2 className="w-4 h-4 text-on-surface-variant" />
                <p className="text-body-sm text-on-surface-variant">回收站 — 删除的项目可在此恢复或永久清除</p>
              </div>
              {trashProjects.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 gap-3">
                  <Trash2 className="w-12 h-12 text-on-surface-variant/40" />
                  <p className="text-body-sm text-on-surface-variant">回收站为空</p>
                </div>
              ) : (
                <div className="grid grid-cols-12 gap-4">
                  {trashProjects.map((proj) => (
                    <div key={proj.id}
                      className="col-span-12 sm:col-span-6 lg:col-span-4 flex items-center gap-3
                        bg-surface-container border border-outline-variant/30 rounded-cw-md p-3
                        hover:border-outline-variant/60 transition-colors">
                      <div className="w-14 h-9 rounded-cw-sm shrink-0 overflow-hidden"
                        style={{ background: `linear-gradient(120deg, ${proj.grad[0]}55, ${proj.grad[1]}44)` }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-body-sm font-medium text-on-surface truncate">{proj.name}</p>
                        <p className="text-caption text-on-surface-variant font-mono">{proj.edited} · {proj.tracks} 轨</p>
                      </div>
                      <button onClick={() => handleRestore(proj)} title="恢复"
                        className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-primary transition-colors cursor-pointer"
                        aria-label={`恢复 ${proj.name}`}>
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handlePurge(proj)} title="永久删除"
                        className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-error transition-colors cursor-pointer"
                        aria-label={`永久删除 ${proj.name}`}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : loading ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <Loader2 className="w-6 h-6 text-primary animate-spin" />
              <span className="text-caption text-on-surface-variant">加载中…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <PackageOpen className="w-12 h-12 text-on-surface-variant/40" />
              <p className="text-body-sm text-on-surface-variant">{error}</p>
              <button onClick={() => navigate({ to: '/' })}
                className="mt-2 text-caption text-primary hover:underline cursor-pointer">返回主页</button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <FolderOpen className="w-12 h-12 text-on-surface-variant/40" />
              <p className="text-body-sm text-on-surface-variant">
                {projects.length === 0 ? '还没有任何项目' : '没有匹配的项目'}
              </p>
              <button onClick={() => navigate({ to: '/' })}
                className="mt-2 text-caption text-primary hover:underline cursor-pointer">返回主页创建项目</button>
            </div>
          ) : (
            <div className="grid grid-cols-12 gap-4">
              {filtered.map((proj) => (
                <ProjectCard
                  key={proj.id}
                  proj={proj}
                  mode="full"
                  folders={folders}
                  onOpen={() => handleOpen(proj)}
                  onDelete={() => handleDelete(proj)}
                  onRename={(name) => handleRename(proj, name)}
                  onSetFolder={(folder) => handleMoveFolder(proj, folder)}
                  onAddTag={(tag) => handleAddTag(proj, tag)}
                  onRemoveTag={(tag) => handleRemoveTag(proj, tag)}
                  onDuplicate={() => handleDuplicate(proj)}
                  onRefreshThumbnail={() => handleRefreshThumbnail(proj)}
                  onArchive={() => handleArchive(proj)}
                />
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

/* ── subcomponents ─────────────────────────────────────── */
function FolderSidebarItem({ label, active, onClick, count, onRename, onDelete }: {
  label: string; active: boolean; onClick: () => void; count: number;
  onRename?: () => void; onDelete?: () => void;
}) {
  return (
    <div className="group flex items-center rounded-cw-xs transition-colors text-left">
      <button onClick={onClick}
        className={`flex-1 flex items-center gap-2 px-2 py-1.5 text-caption cursor-pointer ${
          active
            ? 'bg-primary/10 text-primary font-medium'
            : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container'
        }`}>
        {active ? <FolderOpen className="w-3.5 h-3.5" /> : <Folder className="w-3.5 h-3.5" />}
        <span className="flex-1 truncate">{label}</span>
        <span className="text-on-surface-variant/50">{count}</span>
      </button>
      {onRename && onDelete && (
        <div className="hidden group-hover:flex items-center gap-0.5 pr-1 shrink-0">
          <button onClick={(e) => { e.stopPropagation(); onRename(); }}
            className="p-0.5 rounded text-on-surface-variant/50 hover:text-on-surface transition-colors cursor-pointer"
            title="重命名文件夹">✎</button>
          <button onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="p-0.5 rounded text-on-surface-variant/50 hover:text-error transition-colors cursor-pointer"
            title="删除文件夹">🗑</button>
        </div>
      )}
    </div>
  );
}


