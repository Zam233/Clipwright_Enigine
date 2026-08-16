import { useEffect, useRef, useState } from 'react';
import { StandardLayout } from '@/layouts/StandardLayout';
import { marketApi, type MarketItem, type MarketKind } from '@/services/api/market';
import { toast } from '@/stores/toastStore';

type Tab = 'plugins' | 'personas';

/**
 * MarketPage — P4-4C 市场浏览与发布。
 * 浏览走主项目 /api/market（代理 Server）；发布/评分直连 /srv（需账号登录）。
 */
export function MarketPage() {
  const [tab, setTab] = useState<Tab>('plugins');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<MarketItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [installing, setInstalling] = useState<string>('');
  const [showPublish, setShowPublish] = useState(false);

  const kind: MarketKind = tab === 'plugins' ? 'plugin' : 'persona';

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = tab === 'plugins' ? await marketApi.plugins(q) : await marketApi.personas(q);
      setItems(res.items ?? []);
    } catch {
      setError('市场服务不可用：请确认 ClipWright Server（:8090）已启动');
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const install = async (item: MarketItem) => {
    setInstalling(item.package_id);
    try {
      if (tab === 'plugins') {
        await marketApi.installPlugin(item.package_id, item.version);
        toast(`插件「${item.name}」安装成功`, 'success');
      } else {
        await marketApi.installPersona(item.package_id, item.version);
        toast(`Persona「${item.name}」已导入`, 'success');
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast(typeof detail === 'string' ? detail : '安装失败', 'error');
    } finally {
      setInstalling('');
    }
  };

  return (
    <StandardLayout title="市场">
      <div className="max-w-4xl mx-auto space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex rounded-cw-md overflow-hidden border border-outline-variant/40">
            {(['plugins', 'personas'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-2 text-body-sm font-medium transition-colors cursor-pointer ${
                  tab === t ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                }`}
              >
                {t === 'plugins' ? '插件' : 'Persona'}
              </button>
            ))}
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void load(); }}
            placeholder="搜索名称/描述…"
            className="flex-1 px-3 py-2 rounded-cw-md bg-surface-container text-body-sm"
          />
          <button
            onClick={() => void load()}
            className="px-3 py-2 rounded-cw-md bg-surface-container-highest text-body-sm hover:bg-primary-container/40 cursor-pointer"
          >
            搜索
          </button>
          <button
            onClick={() => setShowPublish((v) => !v)}
            className="px-3 py-2 rounded-cw-md bg-primary text-on-primary text-body-sm font-medium cursor-pointer"
          >
            发布
          </button>
        </div>

        {showPublish && <PublishForm kind={kind} onDone={() => { setShowPublish(false); void load(); }} />}

        {error && <p className="text-caption text-error">{error}</p>}
        {loading && <p className="text-caption text-on-surface-variant">加载中…</p>}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {items.map((it) => (
            <div key={it.package_id} className="p-4 rounded-cw-md bg-surface-container-low border border-outline-variant/30 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-body-sm font-semibold text-on-surface">{it.name}</p>
                  <p className="font-mono text-caption text-on-surface-variant">v{it.version} · {it.package_id}</p>
                </div>
                <span className="text-caption text-track-text shrink-0">
                  ★ {it.rating?.avg ?? '—'}（{it.rating?.count ?? 0}）
                </span>
              </div>
              {it.description && (
                <p className="text-caption text-on-surface-variant leading-snug line-clamp-2">{it.description}</p>
              )}
              <div className="flex items-center gap-1.5 flex-wrap">
                {(it.tags ?? []).slice(0, 6).map((t) => (
                  <span key={t} className="px-1.5 py-0.5 rounded-cw-xs bg-surface-container-high text-caption text-on-surface-variant">{t}</span>
                ))}
              </div>
              <div className="flex items-center justify-between pt-1">
                <span className="text-caption text-on-surface-variant">下载 {it.download_count} · {it.author ?? '—'}</span>
                <button
                  onClick={() => void install(it)}
                  disabled={installing === it.package_id}
                  className="px-2.5 py-1 rounded-cw-sm bg-track-audio/15 text-track-audio text-label-sm font-medium hover:bg-track-audio/25 disabled:opacity-60 cursor-pointer"
                >
                  {installing === it.package_id ? '安装中…' : tab === 'plugins' ? '安装插件' : '导入 Persona'}
                </button>
              </div>
            </div>
          ))}
        </div>
        {!loading && !error && items.length === 0 && (
          <p className="text-body-sm text-on-surface-variant text-center py-8">暂无内容（发布审核通过后可见）</p>
        )}
      </div>
    </StandardLayout>
  );
}

function PublishForm({ kind, onDone }: { kind: MarketKind; onDone: () => void }) {
  const [packageId, setPackageId] = useState('');
  const [version, setVersion] = useState('1.0.0');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('');
  const [licenseName, setLicenseName] = useState('MIT');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!packageId.trim() || !file) {
      toast('请填写包 ID 并选择打包文件（tar.gz，含 plugin.yaml/persona.yaml）', 'error');
      return;
    }
    setBusy(true);
    try {
      await marketApi.publish(kind, {
        package_id: packageId.trim(), version: version.trim() || '1.0.0',
        name: name.trim(), description: description.trim(), tags: tags.trim(), license: licenseName.trim(),
      }, file);
      toast('已提交发布，等待审核', 'success');
      onDone();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast(typeof detail === 'string' ? detail : '发布失败（需登录账号且 Server 在线）', 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-4 rounded-cw-md border border-primary/30 bg-surface-container space-y-3">
      <p className="text-body-sm font-semibold text-on-surface">发布{kind === 'plugin' ? '插件' : 'Persona'}</p>
      <div className="grid grid-cols-2 gap-3">
        <input value={packageId} onChange={(e) => setPackageId(e.target.value)} placeholder="包 ID（如 my_plugin）"
          className="px-3 py-2 rounded-cw-md bg-surface text-body-sm" />
        <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="版本"
          className="px-3 py-2 rounded-cw-md bg-surface text-body-sm" />
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名称"
          className="px-3 py-2 rounded-cw-md bg-surface text-body-sm" />
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="标签（逗号分隔）"
          className="px-3 py-2 rounded-cw-md bg-surface text-body-sm" />
      </div>
      <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="描述"
        rows={2} className="w-full px-3 py-2 rounded-cw-md bg-surface text-body-sm" />
      <div className="flex items-center gap-3">
        <input ref={fileRef} type="file" accept=".tar.gz,.tgz" className="text-caption text-on-surface-variant" />
        <button onClick={() => void submit()} disabled={busy}
          className="px-3 py-1.5 rounded-cw-sm bg-primary text-on-primary text-label-sm font-medium disabled:opacity-60 cursor-pointer">
          {busy ? '提交中…' : '提交发布'}
        </button>
      </div>
    </div>
  );
}
