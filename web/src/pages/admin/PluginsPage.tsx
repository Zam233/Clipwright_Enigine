import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { pluginApi } from '@/services/api';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { PluginConfigField, PluginConfigResponse } from '@/types/api';
import { Puzzle, Power, Loader2, RefreshCw, Zap, Settings, X, Save, Trash2, Eye, Box, AlertTriangle } from 'lucide-react';
import { PluginLayoutRenderer } from '@/features/plugins';
import type { UILayout } from '@/features/plugins/types';

interface PluginItem {
  id: string;
  name: string;
  description?: string;
  version?: string;
  kind?: string;
  loaded: boolean;
  has_ui?: boolean;
}

export function PluginsPage() {
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loadingAll, setLoadingAll] = useState(false);

  const [configId, setConfigId] = useState<string | null>(null);
  const [configFields, setConfigFields] = useState<Record<string, PluginConfigField>>({});
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // M12: 插件 UI 预览
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewLayout, setPreviewLayout] = useState<UILayout | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // M7: 插件错误通道
  const [errorsOpen, setErrorsOpen] = useState(false);
  const [pluginErrors, setPluginErrors] = useState<Awaited<ReturnType<typeof pluginApi.errors>>>([]);
  const [errorsLoading, setErrorsLoading] = useState(false);

  const loadErrors = async () => {
    setErrorsLoading(true);
    try {
      setPluginErrors(await pluginApi.errors(100));
    } catch {
      setPluginErrors([]);
    } finally {
      setErrorsLoading(false);
    }
  };

  const clearErrors = async () => {
    try {
      await pluginApi.clearErrors();
      setPluginErrors([]);
    } catch { /* offline */ }
  };

  const openPreview = async (p: PluginItem) => {
    setPreviewId(p.id);
    setPreviewLayout(null);
    setPreviewError(null);
    setPreviewLoading(true);
    try {
      const ui = await pluginApi.getUI(p.id);
      if (ui && ui.widgets && ui.widgets.length > 0) setPreviewLayout(ui as UILayout);
      else setPreviewError('该插件未定义 UI（ui.json 缺失或为空）');
    } catch (e: unknown) {
      setPreviewError(e instanceof Error ? e.message : '读取插件 UI 失败');
    } finally {
      setPreviewLoading(false);
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const [loaded, discovered] = await Promise.allSettled([
        pluginApi.list(),
        pluginApi.discover(),
      ]);
      const loadedList = loaded.status === 'fulfilled' ? normalize(loaded.value) : [];
      const discoveredIds = discovered.status === 'fulfilled' ? (discovered.value as string[]) : [];
      // 合并：已加载的 + 发现但未加载的
      const loadedIds = new Set(loadedList.map((p) => p.id));
      const unloadedPlugins: PluginItem[] = discoveredIds
        .filter((id) => !loadedIds.has(id))
        .map((id) => ({ id, name: id, kind: 'capability', loaded: false, description: '未加载' }));
      setPlugins([...loadedList, ...unloadedPlugins]);
    } catch {
      setPlugins(DEMO_PLUGINS);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = async (p: PluginItem) => {
    setBusyId(p.id);
    try {
      // M8: 启停持久化 — enable/disable 落盘，重启后保持状态
      if (p.loaded) await pluginApi.disable(p.id);
      else await pluginApi.enable(p.id);
    } catch { /* offline */ }
    setPlugins((ps) => ps.map((x) => (x.id === p.id ? { ...x, loaded: !x.loaded } : x)));
    setBusyId(null);
  };

  const loadAll = async () => {
    setLoadingAll(true);
    try { await pluginApi.loadAll(); } catch { /* offline */ }
    setPlugins((ps) => ps.map((x) => ({ ...x, loaded: true })));
    setLoadingAll(false);
  };

  const openConfig = async (p: PluginItem) => {
    setConfigId(p.id);
    setConfigFields({});
    setConfigError(null);
    setConfigLoading(true);
    try {
      const cfg = await pluginApi.getConfig(p.id) as unknown as PluginConfigResponse;
      setConfigFields(cfg.fields || {});
    } catch (e: unknown) {
      setConfigError(e instanceof Error ? e.message : '读取配置失败');
    } finally {
      setConfigLoading(false);
    }
  };

  const updateField = (key: string, value: unknown) => {
    setConfigFields((prev) => ({
      ...prev,
      [key]: { ...prev[key], value },
    }));
    setConfigError(null);
  };

  const saveConfig = async () => {
    if (!configId) return;
    const yaml = buildConfigYaml(configFields);
    if (!yaml) return;
    setConfigSaving(true);
    setConfigError(null);
    try {
      await pluginApi.saveConfig(configId, yaml);
      closeConfig();
    } catch (e: unknown) {
      const resp = (e as { response?: { status?: number; data?: { detail?: { message?: string; errors?: string[] } | string } } }).response;
      if (resp?.status === 400) {
        const detail = resp.data?.detail;
        const errors = typeof detail === 'object' && detail !== null
          ? (detail as { errors?: string[] }).errors?.join('\n') || (detail as { message?: string }).message
          : String(detail || '');
        setConfigError(`校验失败:\n${errors}`);
      } else {
        setConfigError(e instanceof Error ? e.message : '保存失败');
      }
    } finally {
      setConfigSaving(false);
    }
  };

  const deleteConfig = async () => {
    if (!configId) return;
    setConfigSaving(true);
    setConfigError(null);
    try {
      await pluginApi.deleteConfig(configId);
      const cfg = await pluginApi.getConfig(configId) as unknown as PluginConfigResponse;
      setConfigFields(cfg.fields || {});
    } catch (e: unknown) {
      setConfigError(e instanceof Error ? e.message : '恢复默认失败');
    } finally {
      setConfigSaving(false);
    }
  };

  const closeConfig = () => {
    setConfigId(null);
    setConfigFields({});
    setConfigError(null);
  };

  const loadedCount = plugins.filter((p) => p.loaded).length;

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Extensions / Plugins" title="插件管理"
        desc="加载/卸载插件并编辑运行时配置。配置写入后即时生效，无需重启。" />

      <div className="flex items-center gap-4 mb-6 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-3.5">
        <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center">
          <Puzzle className="w-5 h-5 text-on-primary-container" />
        </span>
        <div className="flex-1">
          <p className="text-body-sm font-semibold text-on-surface">插件机架</p>
          <p className="font-mono text-caption text-on-surface-variant">{loadedCount}/{plugins.length} LOADED</p>
        </div>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} /> 刷新
        </Button>
        <Button size="sm" variant="outline" onClick={() => { setErrorsOpen(true); loadErrors(); }}>
          <AlertTriangle className="w-3.5 h-3.5" /> 错误通道
        </Button>
        <Button size="sm" onClick={loadAll} disabled={loadingAll}>
          {loadingAll ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />} 全部加载
        </Button>
      </div>

      <div className="space-y-2.5 max-w-[820px]">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-20 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : (
          plugins.map((p, i) => (
            <div key={p.id}
              className={cn('relative flex items-center gap-4 bg-surface-container border rounded-cw-md px-5 py-4 overflow-hidden transition-all duration-short3 group hover:-translate-y-0.5',
                p.loaded ? 'border-track-audio/35 hover:shadow-lg hover:shadow-track-audio/5' : 'border-outline-variant/30 opacity-80')}
              style={{ animationDelay: `${i * 40}ms` }}>
              <span className={cn('absolute left-0 top-0 bottom-0 w-1 transition-colors duration-medium2',
                p.loaded ? 'bg-track-audio' : 'bg-outline-variant/40')} />

              <span className={cn('w-10 h-10 rounded-cw-sm flex items-center justify-center shrink-0 transition-colors',
                p.loaded ? 'bg-track-audio/15 text-track-audio' : 'bg-surface-container-high text-on-surface-variant')}>
                <Puzzle className="w-5 h-5" />
              </span>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2.5">
                  <p className="font-mono text-body-sm font-semibold text-on-surface truncate">{p.id}</p>
                  {p.version && <span className="font-mono text-caption text-on-surface-variant shrink-0">v{p.version}</span>}
                  {p.kind && <span className="text-caption text-on-surface-variant/50 font-mono">{p.kind}</span>}
                </div>
                {p.description && <p className="text-caption text-on-surface-variant truncate mt-0.5">{p.description}</p>}
              </div>

              <StatusPill ok={p.loaded} label={p.loaded ? 'ACTIVE' : 'OFF'} />

              {p.loaded && (
                <button onClick={() => openConfig(p)}
                  className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-primary hover:bg-primary/10 transition-colors cursor-pointer shrink-0"
                  title="配置">
                  <Settings className="w-3.5 h-3.5" />
                </button>
              )}

              <button onClick={() => openPreview(p)}
                className="p-1.5 rounded-cw-xs text-on-surface-variant hover:text-primary hover:bg-primary/10 transition-colors cursor-pointer shrink-0"
                title="UI 预览">
                <Eye className="w-3.5 h-3.5" />
              </button>

              <button onClick={() => toggle(p)} disabled={busyId === p.id}
                className={cn('relative w-12 h-[26px] rounded-cw-full transition-colors duration-short3 cursor-pointer shrink-0',
                  p.loaded ? 'bg-track-audio' : 'bg-outline-variant/50')}
                title={p.loaded ? '禁用（持久化）' : '启用（持久化）'}>
                {busyId === p.id ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-white" />
                ) : (
                  <span className={cn('absolute top-[3px] w-5 h-5 rounded-full bg-white shadow transition-all duration-short3 flex items-center justify-center',
                    p.loaded ? 'left-[26px]' : 'left-[3px]')}>
                    <Power className={cn('w-2.5 h-2.5', p.loaded ? 'text-track-audio' : 'text-on-surface-variant')} />
                  </span>
                )}
              </button>
            </div>
          ))
        )}
      </div>

      {configId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={closeConfig}>
          <div className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-surface-container border border-outline-variant/40 rounded-cw-lg p-5 space-y-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h2 className="text-title-sm font-semibold text-on-surface">插件配置 · {configId}</h2>
              <button onClick={closeConfig} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>

            {configLoading ? (
              <div className="flex items-center gap-2 text-label-sm text-on-surface-variant py-4">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> 读取配置…
              </div>
            ) : Object.keys(configFields).length === 0 ? (
              <p className="text-label-sm text-on-surface-variant text-center py-4">该插件暂无配置项。</p>
            ) : (
              <>
                <div className="space-y-3">
                  {Object.entries(configFields).map(([key, field]) => (
                    <div key={key}>
                      <label className="block text-label font-medium text-on-surface-variant mb-1">
                        {field.label || key}
                        {field.description && (
                          <span className="block text-caption text-on-surface-variant/60 font-normal mt-0.5">{field.description}</span>
                        )}
                      </label>
                      {renderFieldControl(key, field, updateField)}
                    </div>
                  ))}
                </div>

                {configError && (
                  <div className="bg-error/10 border border-error/30 rounded-cw-xs px-3 py-2 text-label-sm text-error whitespace-pre-wrap">{configError}</div>
                )}

                <div className="flex gap-2">
                  <Button onClick={saveConfig} disabled={configSaving} className="flex-1">
                    {configSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    保存配置
                  </Button>
                  <Button variant="outline" onClick={deleteConfig} disabled={configSaving} className="flex-1">
                    <Trash2 className="w-4 h-4" />
                    恢复默认
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {errorsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setErrorsOpen(false)}>
          <div className="w-full max-w-lg max-h-[80vh] overflow-y-auto bg-surface-container border border-outline-variant/40 rounded-cw-lg p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="flex items-center gap-2 text-title-sm font-semibold text-on-surface">
                <AlertTriangle className="w-4 h-4 text-warning" /> 插件错误通道
              </h2>
              <div className="flex items-center gap-1">
                <Button size="sm" variant="outline" onClick={clearErrors} disabled={pluginErrors.length === 0}>
                  <Trash2 className="w-3.5 h-3.5" /> 清空
                </Button>
                <button onClick={() => setErrorsOpen(false)} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface cursor-pointer">
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {errorsLoading ? (
              <div className="flex items-center gap-2 text-label-sm text-on-surface-variant py-4">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> 加载错误…
              </div>
            ) : pluginErrors.length === 0 ? (
              <p className="text-label-sm text-on-surface-variant/70 text-center py-6">暂无插件错误。</p>
            ) : (
              <ul className="space-y-2">
                {pluginErrors.map((e, i) => (
                  <li key={i} className="bg-surface rounded-cw-xs border border-error/20 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-label-sm text-on-surface">{e.plugin_id}</span>
                      <span className="text-caption font-mono text-error/80">{e.phase}</span>
                      <span className="text-caption text-on-surface-variant/50 font-mono ml-auto">
                        {new Date(e.ts * 1000).toLocaleTimeString()}
                      </span>
                    </div>
                    <p className="text-label-sm text-on-surface mt-1">{e.message}</p>
                    {e.details && <p className="text-caption text-on-surface-variant/60 font-mono mt-1 break-all">{e.details}</p>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {previewId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setPreviewId(null)}>
          <div className="w-full max-w-md max-h-[80vh] overflow-y-auto bg-surface-container border border-outline-variant/40 rounded-cw-lg p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="flex items-center gap-2 text-title-sm font-semibold text-on-surface">
                <Box className="w-4 h-4 text-primary" /> 插件 UI 预览 · {previewId}
              </h2>
              <button onClick={() => setPreviewId(null)} className="p-1 rounded-cw-xs text-on-surface-variant hover:text-on-surface cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>

            {previewLoading ? (
              <div className="flex items-center gap-2 text-label-sm text-on-surface-variant py-4">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> 加载 UI…
              </div>
            ) : previewError ? (
              <p className="text-label-sm text-error py-4">{previewError}</p>
            ) : previewLayout ? (
              <div className="bg-surface rounded-cw-sm border border-outline-variant/20 p-3">
                <PluginLayoutRenderer layout={previewLayout} />
              </div>
            ) : null}
          </div>
        </div>
      )}
    </ConsoleShell>
  );
}

function normalize(data: unknown): PluginItem[] {
  if (Array.isArray(data)) {
    return data.map((d) => {
      const o = d as Record<string, unknown>;
      const m = (o.manifest ?? o) as Record<string, unknown>;
      return {
        id: String(m.id ?? o.id ?? ''),
        name: String(m.name ?? o.name ?? m.id ?? ''),
        description: m.description ? String(m.description) : undefined,
        version: m.version ? String(m.version) : undefined,
        kind: m.kind ? String(m.kind) : undefined,
        loaded: Boolean(o.enabled ?? o.loaded ?? true),
      };
    });
  }
  return [];
}

function renderFieldControl(
  key: string,
  field: PluginConfigField,
  onChange: (key: string, value: unknown) => void,
) {
  switch (field.type) {
    case 'bool':
      return (
        <button
          onClick={() => onChange(key, !field.value)}
          className={`relative w-11 h-6 rounded-cw-full transition-colors duration-short3 cursor-pointer ${
            field.value ? 'bg-track-audio' : 'bg-outline-variant/50'
          }`}
        >
          <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all duration-short3 ${
            field.value ? 'left-[22px]' : 'left-0.5'
          }`} />
        </button>
      );
    case 'int':
      return (
        <input
          type="number" step={1}
          value={Number(field.value) || 0}
          onChange={(e) => onChange(key, parseInt(e.target.value) || 0)}
          className="w-full bg-surface rounded-cw-xs px-2.5 py-1.5 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary"
        />
      );
    case 'float':
      return (
        <input
          type="number" step={0.1}
          value={Number(field.value) || 0}
          onChange={(e) => onChange(key, parseFloat(e.target.value) || 0)}
          className="w-full bg-surface rounded-cw-xs px-2.5 py-1.5 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary"
        />
      );
    case 'dict':
    case 'list':
      return (
        <textarea
          value={JSON.stringify(field.value, null, 2)}
          onChange={(e) => {
            try { onChange(key, JSON.parse(e.target.value)); } catch { /* ignore */ }
          }}
          rows={4}
          spellCheck={false}
          className="w-full bg-surface rounded-cw-xs border border-outline-variant/30 p-2.5 text-label-sm text-on-surface
            outline-none focus:border-primary resize-y font-mono placeholder:text-on-surface-variant/50"
        />
      );
    default:
      return (
        <input
          type="text"
          value={String(field.value ?? '')}
          onChange={(e) => onChange(key, e.target.value)}
          className="w-full bg-surface rounded-cw-xs px-2.5 py-1.5 text-body-sm text-on-surface
            outline-none border border-outline-variant/30 focus:border-primary"
        />
      );
  }
}

function buildConfigYaml(fields: Record<string, PluginConfigField>): string | null {
  const keys = Object.keys(fields);
  if (keys.length === 0) return null;
  const lines = ['fields:'];
  for (const key of keys) {
    const f = fields[key];
    lines.push(`  ${key}:`);
    lines.push(`    type: ${f.type}`);
    lines.push(`    label: "${String(f.label).replace(/"/g, '\\"')}"`);
    if (f.description) lines.push(`    description: "${String(f.description).replace(/"/g, '\\"')}"`);
    if (f.type === 'string') {
      lines.push(`    value: "${String(f.value ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`);
    } else if (f.type === 'int' || f.type === 'float') {
      lines.push(`    value: ${f.value ?? (f.type === 'int' ? 0 : 0.0)}`);
    } else if (f.type === 'bool') {
      lines.push(`    value: ${f.value ? 'true' : 'false'}`);
    } else {
      lines.push(`    value: ${JSON.stringify(f.value)}`);
    }
  }
  return lines.join('\n');
}

const DEMO_PLUGINS: PluginItem[] = [];
