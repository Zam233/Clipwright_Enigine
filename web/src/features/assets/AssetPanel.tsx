import { memo, useState, useCallback, useEffect, useRef } from 'react';
import { useAssetStore } from '@/stores/assetStore';
import { useTimelineStore } from '@/stores/timelineStore';
import { useProjectStore } from '@/stores/projectStore';
import { useHistoryStore } from '@/stores/historyStore';
import { usePreviewStore } from '@/stores/previewStore';
import { assetApi, visionApi, toolApi } from '@/services/api';
import { mediaManager } from '@/services/media/mediaManager';
import { toast } from '@/stores/toastStore';
import { Button } from '@/components/ui';
import { uid, normalizeClipKind } from '@/lib/utils';
import { DubView } from './DubView';
import { PluginPanel } from './PluginPanel';
import type { Asset, MaterialSearchResult } from '@/types/api';
import type { MaterialAsset } from '@/services/api/asset';
import type { ClipKind } from '@/types/timeline';
import { Sparkles, FolderOpen, History, Upload, Search, Plus, Mic, Puzzle, X, Heart, Check, Info, Trash2, Wand2 } from 'lucide-react';

type Tab = 'ai' | 'library' | 'history' | 'dub' | 'plugins';

const TABS: { id: Tab; label: string; icon: typeof Sparkles }[] = [
  { id: 'ai', label: 'AI 匹配', icon: Sparkles },
  { id: 'library', label: '素材库', icon: FolderOpen },
  { id: 'history', label: '历史', icon: History },
  { id: 'dub', label: '配音', icon: Mic },
  { id: 'plugins', label: '插件', icon: Puzzle },
];

/**
 * AssetPanel — three-tab asset browser (AI match / library / history).
 * Supports double-click or drag to add assets to the timeline.
 */
export function AssetPanel() {
  const activeTab = useAssetStore((s) => s.activeTab);
  const setActiveTab = useAssetStore((s) => s.setActiveTab);
  const assets = useAssetStore((s) => s.assets);
  const setAssets = useAssetStore((s) => s.setAssets);
  const history = useAssetStore((s) => s.history);
  const isLoading = useAssetStore((s) => s.isLoading);
  const setLoading = useAssetStore((s) => s.setLoading);
  const uploadProgress = useAssetStore((s) => s.uploadProgress);
  const setUploadProgress = useAssetStore((s) => s.setUploadProgress);
  const refreshCounter = useAssetStore((s) => s.refreshCounter);
  const [loadError, setLoadError] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const projectId = useProjectStore((s) => s.projectId);
  const loadAssets = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    setDemoMode(false);
    try {
      const list = await assetApi.list(projectId ?? undefined);
      setAssets(Array.isArray(list) ? list : []);
    } catch {
      setAssets(demoAssets());
      setDemoMode(true);
    } finally {
      setLoading(false);
    }
  }, [setAssets, setLoading, projectId]);

  // Load on mount + reload on project change (refreshCounter bump)
  useEffect(() => { loadAssets(); }, [loadAssets, refreshCounter]);

  // M9: 素材删除（移除素材库条目；离线时仅本地移除）
  const handleDeleteAsset = async (asset: Asset) => {
    if (!window.confirm(`从素材库移除「${asset.filename || asset.id}」？（原始文件保留）`)) return;
    try {
      await assetApi.remove(asset.id, projectId ?? undefined);
    } catch {
      /* 离线/后端不可达：仅本地移除 */
    }
    setAssets(useAssetStore.getState().assets.filter((a) => a.id !== asset.id));
  };

  // P8: 特效工具（抠像/背景移除/稳定/水印）— 调用后端 tool 执行，产物重新注册到素材库
  const handleAssetEffect = async (asset: Asset, effect: string) => {
    const inputPath = asset.path || asset.id;
    if (!inputPath) { toast('素材路径缺失，无法处理', 'error'); return; }
    try {
      toast(`正在处理「${asset.filename}」…`, 'info');
      const res = await toolApi.execute(effect, { input_path: inputPath });
      if (res.status !== 'success') {
        toast(`特效处理失败：${res.error ?? '未知错误'}`, 'error');
        return;
      }
      const outPath = (res.output as Record<string, unknown> | undefined)?.output_path
        ?? (res.output as Record<string, unknown> | undefined)?.processed_path
        ?? (res.output as Record<string, unknown> | undefined)?.watermarked_path;
      if (!outPath) { toast('特效完成，但未返回产物路径', 'info'); return; }
      toast('特效处理完成，已加入素材库', 'success');
      await loadAssets();
    } catch {
      toast('特效处理失败（后端离线或工具不可用）', 'error');
    }
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setLoading(true);
    try {
      for (const file of Array.from(files)) {
        const pid = useProjectStore.getState().projectId;
        const res = await assetApi.upload(file, setUploadProgress, pid ?? undefined);
        // Register uploaded file with MediaManager for local preview/thumbnails
        const assetId = res.id || res.asset_id || uid('asset');
        mediaManager.registerFile(assetId, file);
      }
      await loadAssets();
    } catch {
      // W5: 离线/失败 → 本地演示素材（明确标记为演示数据，不静默伪装成后端素材）
      setDemoMode(true);
      const newAssets: Asset[] = Array.from(files).map((f) => {
        const id = uid('asset');
        const kind = normalizeClipKind(f.type.startsWith('video') ? 'video' : f.type.startsWith('audio') ? 'audio' : 'image') as Asset['kind'];
        // Register real media so preview/thumbnails/waveforms work
        mediaManager.registerFile(id, f);
        return {
          id,
          filename: f.name,
          path: f.name,
          kind,
          tags: ['本地上传'],
          created_at: new Date().toISOString(),
        };
      });
      setAssets([...newAssets, ...useAssetStore.getState().assets]);
      toast('后端不可达 — 已添加本地演示素材（不持久化）', 'info');
    } finally {
      setLoading(false);
      setUploadProgress(null);
    }
  };

  const addToTimeline = (asset: Asset, opts?: { ripple?: boolean }) => {
    const store = useTimelineStore.getState();
    const kind: ClipKind = normalizeClipKind(asset.kind);
    // Find or create a matching track
    let track = store.timeline.tracks.find((t) => t.kind === kind);
    if (!track) {
      const tid = store.addTrack(kind);
      track = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
    }
    if (!track) return;
    // Prefer real media duration, but guard against zero (0 is falsy for ??)
    const realDur = mediaManager.getDuration(asset.id);
    const duration = realDur > 0 ? realDur : (asset.duration_sec != null && asset.duration_sec > 0 ? asset.duration_sec : 5);
    const clipData = { kind, asset_id: asset.id, duration_sec: duration, metadata: { title: asset.filename } };

    useHistoryStore.getState().pushState(store.timeline, 'add-asset');
    if (opts?.ripple) {
      // Ripple insert at playhead: shift later clips right
      const atSec = usePreviewStore.getState().currentTimeSec;
      store.rippleInsert(track.id, clipData, atSec);
    } else {
      // Append after last clip on that track
      const lastEnd = track.clips.reduce((m, c) => Math.max(m, c.start_sec + c.duration_sec), 0);
      store.addClip(track.id, { ...clipData, start_sec: lastEnd });
    }
    useAssetStore.getState().addToHistory(asset);
  };

  const filtered = assets.filter((a) =>
    a.filename.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <div className="flex flex-col h-full bg-surface-container-low">
      {/* Tab bar */}
      <div className="flex border-b border-outline-variant/30 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-label font-medium
              border-b-2 transition-colors duration-short3 cursor-pointer ${
                activeTab === id
                  ? 'border-primary text-primary bg-primary/5'
                  : 'border-transparent text-on-surface-variant hover:text-on-surface'
              }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Search + upload */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-outline-variant/20 shrink-0">
        <div className="flex-1 flex items-center gap-2 bg-surface-container rounded-cw-sm px-2.5 py-1.5">
          <Search className="w-3.5 h-3.5 text-on-surface-variant shrink-0" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索素材…"
            className="flex-1 bg-transparent outline-none text-body-sm text-on-surface placeholder:text-on-surface-variant/50"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="p-0.5 text-on-surface-variant/50 hover:text-on-surface cursor-pointer">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
        <label className="p-2 rounded-cw-sm bg-primary-container text-on-primary-container hover:opacity-90 transition-opacity cursor-pointer">
          <Upload className="w-3.5 h-3.5" />
          <input id="asset-upload-input" type="file" multiple className="hidden"
            onChange={(e) => { handleUpload(e.target.files); e.target.value = ''; }} />
        </label>
      </div>

      {/* Upload progress */}
      {uploadProgress !== null && (
        <div className="px-3 py-1.5 shrink-0">
          <div className="h-1 bg-surface-container rounded-cw-full overflow-hidden">
            <div className="h-full bg-primary transition-all duration-medium2" style={{ width: `${uploadProgress}%` }} />
          </div>
        </div>
      )}

      {(demoMode || loadError) && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-warning/10 border-b border-outline-variant/20 shrink-0">
          <span className="text-caption text-warning flex items-center gap-1">
            <Sparkles className="w-3 h-3" />
            {demoMode ? '演示数据' : '加载失败'}
          </span>
          <button
            onClick={loadAssets}
            className="text-caption text-primary hover:text-primary/80 cursor-pointer"
          >
            重试
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-2 min-h-0">
        {activeTab === 'ai' && <AIMatchView />}

        {activeTab === 'dub' && <DubView />}

        {activeTab === 'plugins' && <PluginPanel />}

        {(activeTab === 'library' || activeTab === 'history') && (
          <>
            {isLoading ? (
              <LoadingGrid />
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {(activeTab === 'library' ? filtered : history).map((asset) => (
                  <AssetCard key={asset.id} asset={asset}
                    onAdd={(opts) => addToTimeline(asset, opts)}
                    onDelete={handleDeleteAsset}
                    onEffect={handleAssetEffect} />
                ))}
              </div>
            )}
            {!isLoading && (activeTab === 'library' ? filtered : history).length === 0 && (
              <EmptyAssets onUpload={() => document.getElementById('asset-upload-input')?.click()} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

export const AssetCard = memo(function AssetCard({ asset, onAdd, onDelete, onEffect }: {
  asset: Asset;
  onAdd: (opts?: { ripple?: boolean }) => void;
  onDelete?: (asset: Asset) => void; // M9
  /** P8: 特效工具（抠像/背景移除/稳定/水印） */
  onEffect?: (asset: Asset, effect: string) => void;
}) {
  const kind = normalizeClipKind(asset.kind);
  const kindColor = kind === 'video' ? '#4F8CFF' : kind === 'audio' ? '#34D399' : kind === 'text' ? '#FBBF24' : '#A855F7';
  const [thumb, setThumb] = useState<string | null>(null);
  const [realDur, setRealDur] = useState(0);

  // Load real thumbnail + duration when media becomes available
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const t = await mediaManager.captureThumbnail(asset.id, 0.1);
      if (alive) setThumb(t);
    };
    if (mediaManager.hasRealMedia(asset.id)) {
      load();
      const un = mediaManager.onChange((id) => {
        if (id === asset.id) {
          if (alive) setRealDur(mediaManager.getDuration(asset.id));
          load();
        }
      });
      return () => { alive = false; un(); };
    }
    const un = mediaManager.onChange((id) => { if (id === asset.id) load(); });
    return () => { alive = false; un(); };
  }, [asset.id]);

  const dur = realDur || asset.duration_sec;

  return (
    <div
      onDoubleClick={(e) => onAdd({ ripple: e.shiftKey })}
      draggable
      onDragStart={(e) => {
        const payload = JSON.stringify({
          id: asset.id, kind, filename: asset.filename, duration: (dur ?? 0) > 0 ? dur : 5,
        });
        e.dataTransfer.setData('application/x-clipwright-asset', payload);
        e.dataTransfer.setData('text/plain', payload);
        e.dataTransfer.effectAllowed = 'copy';
      }}
      className="group bg-surface-container rounded-cw-sm overflow-hidden border border-outline-variant/20
        hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5 transition-all duration-short3 cursor-grab active:cursor-grabbing"
      title="双击添加到时间轴 · Shift+双击在播放头处波纹插入 · 拖拽到时间轴"
    >
      {/* Thumbnail */}
      <div
        className="relative h-16 flex items-center justify-center overflow-hidden"
        style={{ background: `linear-gradient(135deg, ${kindColor}22, ${kindColor}0D)` }}
      >
        {thumb ? (
          <img src={thumb} alt="" draggable={false} className="absolute inset-0 w-full h-full object-cover" />
        ) : asset.thumbnail_url ? (
          <img src={asset.thumbnail_url} alt="" draggable={false} className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <span className="text-2xl" style={{ color: kindColor }}>
            {kind === 'video' ? '🎬' : kind === 'audio' ? '🎵' : kind === 'text' ? '📝' : '🖼'}
          </span>
        )}
        {dur != null && dur > 0 && (
          <span className="absolute bottom-1 right-1 text-caption font-mono bg-black/60 text-white px-1 rounded-cw-xs">
            {dur.toFixed(1)}s
          </span>
        )}
        {/* Hover overlay (visual only, no pointer events — preserves drag from thumbnail) */}
        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-short3 pointer-events-none" />
        {/* Add button — Shift+click = ripple insert at playhead. stopPropagation prevents dblclick on parent. */}
        <button
          onClick={(e) => { e.stopPropagation(); onAdd({ ripple: e.shiftKey }); }}
          className="absolute bottom-1 left-1 w-7 h-7 rounded-cw-full bg-primary text-on-primary flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-short3 pointer-events-auto cursor-pointer"
        >
          <Plus className="w-4 h-4" />
        </button>
        {/* M9: 删除素材（保留原始文件，仅移除素材库条目与软链接） */}
        {onDelete && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(asset); }}
            className="absolute top-1 right-1 w-7 h-7 rounded-cw-full bg-black/60 text-white flex items-center justify-center
              opacity-0 group-hover:opacity-100 transition-opacity duration-short3 pointer-events-auto cursor-pointer hover:bg-error"
            title="从素材库移除"
            aria-label="从素材库移除"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
        {/* P8: 特效工具菜单（仅视频素材） */}
        {onEffect && (kind === 'video' || kind === 'image') && (
          <EffectMenu asset={asset} onEffect={onEffect} />
        )}
      </div>
      <div className="px-2 py-1.5">
        <p className="text-label-sm text-on-surface truncate">{asset.filename}</p>
        <p className="text-caption text-on-surface-variant capitalize">{asset.kind}</p>
      </div>
    </div>
  );
});

/** P8: 特效工具下拉（抠像/背景移除/稳定/水印） */
function EffectMenu({ asset, onEffect }: {
  asset: Asset;
  onEffect: (asset: Asset, effect: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler, true);
    return () => document.removeEventListener('mousedown', handler, true);
  }, [open]);

  const EFFECTS = [
    { id: 'background_remove', label: '移除背景（绿幕）', icon: '🎬' },
    { id: 'video_stabilize', label: '画面稳定', icon: '📷' },
    { id: 'watermark', label: '添加水印', icon: '©' },
  ];

  return (
    <div ref={ref} className="absolute top-1 left-1 z-20">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="w-7 h-7 rounded-cw-full bg-black/60 text-white flex items-center justify-center
          opacity-0 group-hover:opacity-100 transition-opacity duration-short3 pointer-events-auto cursor-pointer hover:bg-primary"
        title="特效工具"
        aria-label={`特效工具 ${asset.filename}`}
      >
        <Wand2 className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 w-44 bg-surface-container-high border border-outline-variant/40
          rounded-cw-sm shadow-xl overflow-hidden text-caption text-on-surface"
          onClick={(e) => e.stopPropagation()}>
          {EFFECTS.map((ef) => (
            <button key={ef.id}
              onClick={() => { onEffect(asset, ef.id); setOpen(false); }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-primary/10 transition-colors cursor-pointer">
              <span>{ef.icon}</span>
              <span className="flex-1">{ef.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AIMatchView() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<MaterialSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);
  const [selSources, setSelSources] = useState<string[]>([]);
  const [visionOpen, setVisionOpen] = useState(false);
  const [visionPath, setVisionPath] = useState('');
  const [visionLoading, setVisionLoading] = useState(false);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [faving, setFaving] = useState<Set<string>>(new Set());
  const projectId = useProjectStore((s) => s.projectId);
  // Material detail popup (GET /api/material/asset/{source}/{id})
  const [detail, setDetail] = useState<{ source: string; id: string; title: string } | null>(null);
  const [detailData, setDetailData] = useState<MaterialAsset | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadDetail = async (source: string, id: string, title: string) => {
    setDetail({ source, id, title });
    setDetailLoading(true);
    setDetailData(null);
    setDetailError(null);
    try {
      const d = await assetApi.getMaterialAsset(source, id);
      setDetailData(d);
    } catch (err: unknown) {
      setDetailError(err instanceof Error ? err.message : '素材详情不可用');
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleFavorite = async (r: MaterialSearchResult, e: React.MouseEvent) => {
    e.stopPropagation();
    const id = r.id;
    if (faving.has(id)) return;
    if (favorites.has(id)) {
      setFavorites((prev) => { const n = new Set(prev); n.delete(id); return n; });
      return;
    }
    setFaving((prev) => new Set(prev).add(id));
    try {
      await assetApi.importUrl(r.url, r.title, projectId ?? undefined);
      setFavorites((prev) => new Set(prev).add(id));
    } catch {
      // silently fail, user can retry
    } finally {
      setFaving((prev) => { const n = new Set(prev); n.delete(id); return n; });
    }
  };
  const [visionResult, setVisionResult] = useState<string | null>(null);
  const searchSeqRef = useRef(0);

  useEffect(() => { assetApi.listSources().then(setSources).catch(() => {}); }, []);

  const doSearch = async (q: string) => {
    const queryText = q.trim() || '通用 B-roll 空镜';
    const seq = ++searchSeqRef.current;
    setSearching(true);
    setSearched(true);
    try {
      const res = await assetApi.searchMaterials({ query: queryText, limit: 12, source: selSources.length > 0 ? selSources : undefined });
      if (seq !== searchSeqRef.current) return; // 忽略过期响应
      setResults(Array.isArray(res) ? res : []);
    } catch {
      if (seq !== searchSeqRef.current) return;
      setResults(demoMatches(queryText));
    } finally {
      if (seq === searchSeqRef.current) setSearching(false);
    }
  };

  const doVisionImport = async () => {
    if (!visionPath.trim()) return;
    setVisionLoading(true);
    setVisionResult(null);
    try {
      const ad = await visionApi.analyze(visionPath.trim());
      const labels = ad.tags?.join(', ') || ad.description || JSON.stringify(ad).slice(0, 200);
      setVisionResult(`分析结果: ${labels}`);
      await visionApi.importImage(visionPath.trim());
      setVisionResult((prev) => `${prev} — 已导入素材库`);
      assetApi.listSources().then(setSources).catch(() => {});
    } catch (e: unknown) {
      setVisionResult(`失败: ${e instanceof Error ? e.message : '未知错误'}`);
    } finally { setVisionLoading(false); }
  };

  const addResult = (r: MaterialSearchResult) => {
    const store = useTimelineStore.getState();
    const kind = normalizeClipKind('video'); // MaterialSearchResult 无 kind 字段，默认为 video
    // 先快照历史再建轨道，避免撤销后残留空轨道
    useHistoryStore.getState().pushState(store.timeline, 'ai-match');
    let track = store.timeline.tracks.find((t) => t.kind === kind);
    if (!track) {
      const tid = store.addTrack(kind);
      track = useTimelineStore.getState().timeline.tracks.find((t) => t.id === tid);
    }
    if (!track) return;
    const lastEnd = track.clips.reduce((m, c) => Math.max(m, c.start_sec + c.duration_sec), 0);
    store.addClip(track.id, {
      kind, asset_id: r.id, start_sec: lastEnd,
      duration_sec: r.duration_sec != null && r.duration_sec > 0 ? r.duration_sec : 5, metadata: { title: r.title, source: r.source },
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex gap-1.5 px-1 pb-2">
        <input value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch(query)}
          placeholder="描述想要的画面，如「城市夜景延时」…"
          className="flex-1 bg-surface-container rounded-cw-sm px-2.5 py-1.5 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
        <button onClick={() => doSearch(query)} disabled={searching}
          className="px-2.5 rounded-cw-sm bg-primary-container text-on-primary-container hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer">
          <Sparkles className="w-3.5 h-3.5" />
        </button>
      </div>

      {sources.length > 0 && (
        <div className="flex flex-wrap gap-1 pb-2 px-1">
          {sources.map((s) => {
            const sel = selSources.includes(s.id);
            return (
              <button key={s.id} onClick={() => setSelSources(sel ? selSources.filter((x) => x !== s.id) : [...selSources, s.id])}
                className={`px-1.5 py-0.5 rounded-cw-full text-caption border transition-colors cursor-pointer ${
                  sel ? 'bg-track-video/15 border-track-video/60 text-track-video' : 'bg-surface-container-high border-outline-variant/40 text-on-surface-variant/70 hover:text-on-surface'
                }`}>
                {s.name}
              </button>
            );
          })}
          <button onClick={() => setVisionOpen(!visionOpen)}
            className="px-1.5 py-0.5 rounded-cw-full text-caption border border-outline-variant/40 bg-surface-container-high
              text-on-surface-variant/70 hover:text-tertiary hover:border-tertiary/40 transition-colors cursor-pointer">
            视觉识别
          </button>
        </div>
      )}

      {visionOpen && (
        <div className="px-1 pb-2 border border-outline-variant/20 rounded-cw-sm p-2 mb-2 bg-surface-container">
          <p className="text-caption text-on-surface-variant mb-1.5">视觉识别：输入图片路径，AI 分析后自动入库</p>
          <div className="flex gap-1.5 mb-1.5">
            <input value={visionPath} onChange={(e) => setVisionPath(e.target.value)}
              placeholder="图片路径…" className="flex-1 bg-surface rounded-cw-xs px-2 py-1 text-label-sm text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
            <Button size="sm" onClick={doVisionImport} disabled={visionLoading || !visionPath.trim()}>
              {visionLoading ? '分析中…' : '导入'}
            </Button>
          </div>
          {visionResult && <p className="text-caption text-on-surface-variant leading-relaxed">{visionResult}</p>}
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-1.5 min-h-0">
        {searching && (
          <div className="flex items-center gap-2 text-label-sm text-on-surface-variant py-4 justify-center">
            <span className="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            语义检索中…
          </div>
        )}
        {!searching && results.map((r) => (
          <button key={r.id} onClick={() => addResult(r)}
            draggable
            onDragStart={(e) => {
              const payload = JSON.stringify({
                id: r.id, kind: 'video', filename: r.title, duration: r.duration_sec != null && r.duration_sec > 0 ? r.duration_sec : 5,
              });
              e.dataTransfer.setData('application/x-clipwright-asset', payload);
              e.dataTransfer.setData('text/plain', payload);
              e.dataTransfer.effectAllowed = 'copy';
            }}
            className="w-full flex items-center gap-2.5 bg-surface-container border border-outline-variant/20 rounded-cw-sm px-2.5 py-2
              hover:border-primary/50 hover:bg-surface transition-all duration-short3 cursor-pointer group text-left">
            {r.thumbnail ? (
              <span className="w-10 h-10 rounded-cw-xs bg-surface overflow-hidden shrink-0">
                <img src={r.thumbnail} alt="" className="w-full h-full object-cover" />
              </span>
            ) : (
              <span className="w-9 h-9 rounded-cw-xs bg-track-video/15 text-track-video flex items-center justify-center shrink-0 text-body">🎬</span>
            )}
            <span className="flex-1 min-w-0">
              <span className="block text-label-sm text-on-surface truncate group-hover:text-primary transition-colors">{r.title}</span>
              <span className="flex items-center gap-1.5 text-caption text-on-surface-variant">
                <span className="font-mono text-track-audio">{Math.round(r.score * 100)}%</span>
                <span>· {r.source}</span>
                {r.duration_sec != null && <span>· {r.duration_sec.toFixed(1)}s</span>}
              </span>
              {r.reason && <span className="block text-caption text-on-surface-variant/70 truncate">{r.reason}</span>}
            </span>
            <Plus className="w-3.5 h-3.5 text-on-surface-variant group-hover:text-primary shrink-0" />
            <button
              onClick={(e) => { e.stopPropagation(); loadDetail(r.source, r.id, r.title); }}
              title="查看素材详情"
              className="p-1 rounded-cw-full transition-colors cursor-pointer shrink-0 text-on-surface-variant/30 hover:text-primary"
            >
              <Info className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={(e) => toggleFavorite(r, e)}
              title={favorites.has(r.id) ? '已收藏' : '收藏到素材库'}
              className={`p-1 rounded-cw-full transition-colors cursor-pointer shrink-0 ${
                favorites.has(r.id) ? 'text-error hover:text-error/80' : 'text-on-surface-variant/30 hover:text-error'
              }`}
            >
              {faving.has(r.id)
                ? <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin block" />
                : favorites.has(r.id) ? <Check className="w-3 h-3" /> : <Heart className="w-3 h-3" />}
            </button>
          </button>
        ))}
        {!searching && searched && results.length === 0 && (
          <p className="text-label-sm text-on-surface-variant text-center py-6">未找到匹配素材</p>
        )}
        {!searching && !searched && (
          <div className="flex flex-col items-center justify-center py-8 text-center px-2">
            <Sparkles className="w-6 h-6 text-primary/50 mb-2" />
            <p className="text-label-sm text-on-surface-variant leading-relaxed">
              输入画面描述，Agent 将从素材源中语义检索最匹配的候选。
            </p>
          </div>
        )}
      </div>

      {/* Material detail popup */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setDetail(null)}>
          <div className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-surface-container border border-outline-variant/40 rounded-cw-lg p-5 space-y-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-title-sm font-semibold text-on-surface truncate">{detailData?.title || detail.title}</h3>
              <button onClick={() => setDetail(null)} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface cursor-pointer shrink-0">
                <X className="w-4 h-4" />
              </button>
            </div>

            {detailLoading ? (
              <div className="flex items-center justify-center gap-2 text-label-sm text-on-surface-variant py-8">
                <span className="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin" /> 加载素材详情…
              </div>
            ) : detailError ? (
              <div className="flex items-center justify-between bg-error/10 border border-error/30 rounded-cw-xs px-3 py-2.5">
                <span className="text-label-sm text-error">{detailError}</span>
                <button
                  onClick={() => detail && loadDetail(detail.source, detail.id, detail.title)}
                  className="text-label-sm text-error hover:text-error/80 cursor-pointer">
                  重试
                </button>
              </div>
            ) : detailData ? (
              <MaterialDetail data={detailData} />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function MaterialDetail({ data }: { data: MaterialAsset }) {
  const thumb = data.thumbnail_url || data.url;
  const size = data.file_size_bytes != null
    ? data.file_size_bytes >= 1024 * 1024
      ? `${(data.file_size_bytes / 1024 / 1024).toFixed(1)} MB`
      : `${Math.max(1, Math.round(data.file_size_bytes / 1024))} KB`
    : null;
  const rows: { label: string; value: string }[] = [
    { label: '类型', value: data.type },
    { label: '来源', value: data.source },
    ...(data.duration_sec != null ? [{ label: '时长', value: `${data.duration_sec.toFixed(1)}s` }] : []),
    ...(data.resolution ? [{ label: '分辨率', value: data.resolution }] : []),
    ...(size ? [{ label: '文件大小', value: size }] : []),
    ...(data.local_path ? [{ label: '本地路径', value: data.local_path }] : []),
    ...(data.url ? [{ label: 'URL', value: data.url }] : []),
    ...(data.created_at ? [{ label: '创建时间', value: data.created_at }] : []),
  ];
  return (
    <div className="space-y-4">
      {thumb && (
        <div className="rounded-cw-sm overflow-hidden bg-surface">
          <img src={thumb} alt={data.title} className="w-full max-h-56 object-contain" />
        </div>
      )}
      <div className="space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex gap-3 text-label-sm">
            <span className="text-on-surface-variant w-20 shrink-0">{r.label}</span>
            <span className="text-on-surface font-mono break-all">{r.value}</span>
          </div>
        ))}
      </div>
      {data.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.tags.map((t) => (
            <span key={t} className="px-2 py-0.5 rounded-cw-full text-caption bg-primary-container/60 text-on-primary-container">{t}</span>
          ))}
        </div>
      )}
      {Object.keys(data.metadata).length > 0 && (
        <div>
          <p className="text-label text-on-surface-variant mb-1.5">元数据</p>
          <pre className="bg-surface rounded-cw-sm border border-outline-variant/30 px-3 py-2 font-mono text-caption
            text-track-audio leading-relaxed max-h-40 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(data.metadata, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function demoMatches(query: string): MaterialSearchResult[] {
  const base = [
    { title: `${query} · 空镜 01`, score: 0.94, reason: '语义高度匹配，色调偏冷' },
    { title: `${query} · 特写 02`, score: 0.88, reason: '构图与主题相关' },
    { title: '城市延时 · 夜景', score: 0.81, reason: '氛围匹配' },
    { title: '数据可视化 · 图表动画', score: 0.74, reason: '可用于论点支撑' },
    { title: '人物采访 · 中景', score: 0.68, reason: '叙事补充' },
  ];
  return base.map((b, i) => ({
    id: `match_${i}_${Date.now().toString(36)}`, title: b.title, url: '', score: b.score,
    source: 'pexels', duration_sec: 4 + i * 2, reason: b.reason,
  }));
}

function LoadingGrid() {
  return (
    <div className="grid grid-cols-2 gap-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="bg-surface-container rounded-cw-sm overflow-hidden animate-pulse">
          <div className="h-16 bg-surface-container-high" />
          <div className="px-2 py-1.5 space-y-1">
            <div className="h-2 bg-surface-container-high rounded w-3/4" />
            <div className="h-2 bg-surface-container-high rounded w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyAssets({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <FolderOpen className="w-8 h-8 text-on-surface-variant/40 mb-2" />
      <p className="text-body-sm text-on-surface-variant mb-3">素材库为空</p>
      <Button size="sm" onClick={onUpload}>
        <Upload className="w-3.5 h-3.5" />
        上传素材
      </Button>
    </div>
  );
}

/** Demo assets so the editor is usable without a backend. */
function demoAssets(): Asset[] {
  const mk = (name: string, kind: Asset['kind'], dur?: number): Asset => ({
    id: uid('asset'), filename: name, path: name, kind,
    duration_sec: dur, tags: ['示例'], created_at: new Date().toISOString(),
  });
  return [
    mk('开场镜头.mp4', 'video', 6),
    mk('产品特写.mp4', 'video', 4.5),
    mk('B-roll_城市.mp4', 'video', 8),
    mk('采访片段.mp4', 'video', 12),
    mk('背景音乐.mp3', 'audio', 60),
    mk('旁白配音.wav', 'audio', 45),
    mk('封面图.png', 'image'),
    mk('LOGO.png', 'image'),
  ];
}
