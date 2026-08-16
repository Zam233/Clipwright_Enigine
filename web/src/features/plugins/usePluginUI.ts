/**
 * usePluginUI — Fetch a plugin's UI layout definition from the backend.
 *
 * Usage:
 *   const { layout, loading, error } = usePluginUI(pluginId);
 *   if (layout) return <PluginLayoutRenderer layout={layout} />;
 */
import { useEffect, useState } from 'react';
import { getApiClient } from '@/services/api';
import type { UILayout } from './types';

export function usePluginUI(pluginId: string) {
  const [layout, setLayout] = useState<UILayout | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // 切换插件时清空旧布局，避免上一个插件的 UI 残留
    setLayout(null);
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const { data } = await getApiClient().get(`/api/plugin/${pluginId}/ui`);
        if (!alive) return;
        if (data && data.widgets) {
          setLayout(data as UILayout);
        } else {
          setLayout(null);
        }
      } catch {
        if (alive) {
          setError('无法加载插件界面');
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [pluginId]);

  return { layout, loading, error };
}
