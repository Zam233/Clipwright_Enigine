import { useState, useEffect } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { StandardLayout } from '@/layouts/StandardLayout';
import { useSettingsStore } from '@/stores/settingsStore';
import { healthApi, assetApi, resetApiClient } from '@/services/api';
import { Button, Badge, Slider } from '@/components/ui';
import { Server, Palette, Ruler, Save, RefreshCw, Terminal, ChevronRight, FolderOpen, Clapperboard, Film, Captions, GraduationCap } from 'lucide-react';

/**
 * SettingsPage — global configuration (API, theme, timeline defaults).
 */
export function SettingsPage() {
  const s = useSettingsStore();
  const navigate = useNavigate();
  const [health, setHealth] = useState<'idle' | 'checking' | 'ok' | 'fail'>('idle');
  const [matSources, setMatSources] = useState<{ id: string; name: string }[]>([]);
  const [matLoading, setMatLoading] = useState(false);

  const loadMatSources = async () => {
    setMatLoading(true);
    try { setMatSources(await assetApi.listSources()); } catch { /* offline */ }
    setMatLoading(false);
  };

  useEffect(() => { loadMatSources(); }, []);

  const checkHealth = async () => {
    setHealth('checking');
    try {
      await healthApi.check();
      setHealth('ok');
    } catch {
      setHealth('fail');
    }
  };

  return (
    <StandardLayout title="全局设置">
      <div className="max-w-2xl mx-auto space-y-6">
        {/* API connection */}
        <Card icon={<Server className="w-4 h-4" />} title="后端连接">
          <Field label="API 地址">
            <input
              value={s.apiBaseUrl}
              onChange={(e) => s.setApiBaseUrl(e.target.value)}
              onBlur={resetApiClient}
              className="w-full bg-surface-container rounded-cw-sm px-3 py-2 text-body-sm font-mono text-on-surface
                outline-none border border-outline-variant/30 focus:border-primary"
            />
          </Field>
          <div className="flex items-center gap-3">
            <Button size="sm" variant="outline" onClick={checkHealth}>
              <RefreshCw className={`w-3.5 h-3.5 ${health === 'checking' ? 'animate-spin' : ''}`} />
              测试连接
            </Button>
            {health === 'ok' && <Badge variant="success">引擎在线</Badge>}
            {health === 'fail' && <Badge variant="error">无法连接</Badge>}
          </div>
        </Card>

        {/* Appearance */}
        <Card icon={<Palette className="w-4 h-4" />} title="外观">
          <Field label="主题">
            <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
              {(['dark', 'light'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => s.setTheme(t)}
                  className={`px-4 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                    s.theme === t ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {t === 'dark' ? '暗色（推荐）' : '亮色'}
                </button>
              ))}
            </div>
          </Field>
          <Field label="界面语言">
            <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
              {(['zh', 'en'] as const).map((l) => (
                <button
                  key={l}
                  onClick={() => s.setLanguage(l)}
                  className={`px-4 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                    s.language === l ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {l === 'zh' ? '中文' : 'English'}
                </button>
              ))}
            </div>
          </Field>
        </Card>

        {/* Timeline defaults */}
        <Card icon={<Ruler className="w-4 h-4" />} title="时间轴默认值">
          <Slider label="默认帧率" min={24} max={60} step={1} value={s.defaultFps} onChange={s.setDefaultFps} />
          <Slider label="吸附阈值" min={2} max={20} step={1} value={s.snapThresholdPx} onChange={s.setSnapThreshold} />
          <Field label="默认分辨率">
            <div className="flex gap-2">
              {[
                { w: 1920, h: 1080, label: '1080p' },
                { w: 3840, h: 2160, label: '4K' },
                { w: 1080, h: 1920, label: '竖屏' },
              ].map((r) => (
                <button
                  key={r.label}
                  onClick={() => s.setDefaultResolution({ width: r.w, height: r.h })}
                  className={`px-3 py-1.5 rounded-cw-sm text-label-sm border transition-colors cursor-pointer ${
                    s.defaultResolution.width === r.w && s.defaultResolution.height === r.h
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-outline-variant/40 text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </Field>
        </Card>

        {/* Saving */}
        <Card icon={<Save className="w-4 h-4" />} title="保存">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-body-sm text-on-surface">自动保存</p>
              <p className="text-label-sm text-on-surface-variant">编辑时自动写入本地缓存</p>
            </div>
            <button
              onClick={() => s.setAutoSave(!s.autoSave)}
              className={`relative w-11 h-6 rounded-cw-full transition-colors duration-short3 cursor-pointer ${
                s.autoSave ? 'bg-primary' : 'bg-outline-variant'
              }`}
            >
              <span
                className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all duration-short3 ${
                  s.autoSave ? 'left-[22px]' : 'left-0.5'
                }`}
              />
            </button>
          </div>
        </Card>

        {/* Material Sources */}
        <Card icon={<FolderOpen className="w-4 h-4" />} title="素材源">
          {matSources.length === 0 && !matLoading && (
            <p className="text-label-sm text-on-surface-variant">暂无注册素材源。启动后端后将自动发现可用源（如 Pexels、本地素材库）。</p>
          )}
          {matLoading && <p className="text-label-sm text-on-surface-variant">加载中…</p>}
          {matSources.map((src) => (
            <div key={src.id} className="flex items-center justify-between bg-surface rounded-cw-xs px-3 py-2 border border-outline-variant/20">
              <span className="text-body-sm text-on-surface">{src.name}</span>
              <span className="font-mono text-caption text-on-surface-variant">{src.id}</span>
            </div>
          ))}
          <Button size="sm" variant="outline" onClick={loadMatSources} disabled={matLoading}>
            <RefreshCw className={`w-3.5 h-3.5 ${matLoading ? 'animate-spin' : ''}`} /> 刷新素材源
          </Button>
        </Card>

        {/* system console entry */}
        <button
          onClick={() => navigate({ to: '/settings/models' })}
          className="w-full flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-4
            hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10 transition-all duration-short3 cursor-pointer group text-left"
        >
          <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
            <Terminal className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">系统控制台</p>
            <p className="text-caption text-on-surface-variant">模型测试 · 工具技能 · 插件 · 类型 · 模板 · 学习训练 · Webhook · 字体 · 管线监控</p>
          </div>
          <ChevronRight className="w-4.5 h-4.5 text-on-surface-variant group-hover:text-primary group-hover:translate-x-1 transition-all shrink-0" />
        </button>

        {/* learning entry */}
        <button
          onClick={() => navigate({ to: '/settings/learning' })}
          className="w-full flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-4
            hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10 transition-all duration-short3 cursor-pointer group text-left"
        >
          <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
            <GraduationCap className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">学习训练</p>
            <p className="text-caption text-on-surface-variant">数据集 · 任务 · 模型 · 后端状态</p>
          </div>
          <ChevronRight className="w-4.5 h-4.5 text-on-surface-variant group-hover:text-primary group-hover:translate-x-1 transition-all shrink-0" />
        </button>

        {/* preprocess entry */}
        <button
          onClick={() => navigate({ to: '/settings/preprocess' })}
          className="w-full flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-4
            hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10 transition-all duration-short3 cursor-pointer group text-left"
        >
          <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
            <Clapperboard className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">素材预处理</p>
            <p className="text-caption text-on-surface-variant">元数据提取 · 场景检测 · 缩略图 · 音频/BPM · 转写</p>
          </div>
          <ChevronRight className="w-4.5 h-4.5 text-on-surface-variant group-hover:text-primary group-hover:translate-x-1 transition-all shrink-0" />
        </button>

        {/* video editor entry */}
        <button
          onClick={() => navigate({ to: '/settings/video-editor' })}
          className="w-full flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-4
            hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10 transition-all duration-short3 cursor-pointer group text-left"
        >
          <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
            <Film className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">视频编辑器控制台</p>
            <p className="text-caption text-on-surface-variant">项目/会话 · undo/redo · clips 增删移分 · 导出 · proxy · 波形</p>
          </div>
          <ChevronRight className="w-4.5 h-4.5 text-on-surface-variant group-hover:text-primary group-hover:translate-x-1 transition-all shrink-0" />
        </button>

        {/* subtitle tools entry */}
        <button
          onClick={() => navigate({ to: '/settings/subtitle-tools' })}
          className="w-full flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-4
            hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10 transition-all duration-short3 cursor-pointer group text-left"
        >
          <span className="w-10 h-10 rounded-cw-sm bg-primary-container flex items-center justify-center shrink-0">
            <Captions className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">字幕与转写</p>
            <p className="text-caption text-on-surface-variant">音频转写 · 文案对齐 · SRT 导入导出 · STT 端点</p>
          </div>
          <ChevronRight className="w-4.5 h-4.5 text-on-surface-variant group-hover:text-primary group-hover:translate-x-1 transition-all shrink-0" />
        </button>
      </div>
    </StandardLayout>
  );
}

function Card({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-outline-variant/20 text-on-surface-variant">
        {icon}
        <h2 className="text-title-sm font-medium text-on-surface">{title}</h2>
      </header>
      <div className="p-4 space-y-4">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-label font-medium text-on-surface-variant mb-1.5">{label}</label>
      {children}
    </div>
  );
}
