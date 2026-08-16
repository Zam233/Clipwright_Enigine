import { useEffect, useState } from 'react';
import { pluginApi } from '@/services/api';
import { usePluginUI } from '@/features/plugins';
import { PluginLayoutRenderer } from '@/features/plugins';
import { Image, Video, Music, Loader2, Puzzle } from 'lucide-react';

/** Plugin icon map — maps plugin_id to icon component */
const PLUGIN_ICONS: Record<string, typeof Image> = {
  ai_image_gen: Image,
  ai_video_gen: Video,
  ai_music_gen: Music,
};

interface PluginInfo {
  id: string;
  label: string;
  pluginId: string;
}

/**
 * PluginPanel — 插件编辑器 UI 面板。
 * 从后端获取已加载的能力插件列表，每个插件的 UI 由其 ui.json 定义驱动。
 */
export function PluginPanel() {
  const [tabs, setTabs] = useState<PluginInfo[]>([]);
  const [activeTab, setActiveTab] = useState<string>('');
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const plugins = await pluginApi.list();
        if (!alive) return;
        const loaded = (Array.isArray(plugins) ? plugins : [])
          .filter((p: Record<string, unknown>) => p.has_ui === true)
          .map((p: Record<string, unknown>) => {
            const m = (p.manifest as Record<string, unknown>) || {};
            const id = String(m.id || '');
            const name = String(m.name || id);
            // Derive tab ID prefix from plugin ID
            const prefix = id.replace(/_gen$/, '').replace(/^ai_/, '');
            return { id: prefix, label: name, pluginId: id };
          });
        if (alive) {
          setTabs(loaded);
          if (loaded.length > 0) setActiveTab(loaded[0].pluginId);
          setChecking(false);
        }
      } catch {
        // Offline: fall back to empty (no hardcoded tabs)
        if (alive) {
          setTabs([]);
          setChecking(false);
        }
      }
    })();
    return () => { alive = false; };
  }, []);

  if (checking) {
    return (
      <div className="flex items-center justify-center py-10 text-on-surface-variant gap-2">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span className="text-caption">加载插件…</span>
      </div>
    );
  }

  if (tabs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-on-surface-variant gap-2">
        <Puzzle className="w-8 h-8 opacity-30" />
        <span className="text-caption">暂无可用插件</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-surface-container-low">
      {/* TAB 栏 */}
      <div className="flex border-b border-outline-variant/30 shrink-0">
        {tabs.map((t) => {
          const Icon = PLUGIN_ICONS[t.pluginId] || Puzzle;
          return (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.pluginId)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-label font-medium
                border-b-2 transition-colors duration-short3 cursor-pointer ${
                  activeTab === t.pluginId
                    ? 'border-primary text-primary bg-primary/5'
                    : 'border-transparent text-on-surface-variant hover:text-on-surface'
                }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* 插件内容区 — 每个 tab 使用 usePluginUI 动态渲染 */}
      <div className="flex-1 overflow-y-auto p-3 min-h-0">
        {tabs.map((t) => (
          <div key={t.pluginId} className={activeTab === t.pluginId ? '' : 'hidden'}>
            <PluginTabView pluginId={t.pluginId} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PluginTabView({ pluginId }: { pluginId: string }) {
  const { layout, loading, error } = usePluginUI(pluginId);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 className="w-4 h-4 animate-spin text-primary" />
      </div>
    );
  }

  if (error || !layout) {
    return (
      <div className="text-caption text-on-surface-variant/60 text-center py-6">
        {error || '插件界面未定义'}
      </div>
    );
  }

  return <PluginLayoutRenderer layout={layout} />;
}
