import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { webhookApi } from '@/services/api';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Webhook, Plus, Send, Trash2, Radio } from 'lucide-react';

interface Subscription {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  lastDelivery?: string;
}

const FALLBACK_EVENT_TYPES = ['pipeline.completed', 'pipeline.failed', 'render.completed'];

/**
 * WebhooksPage — manage outbound event subscriptions and fire test payloads.
 * Backed by /api/webhook/* (list/register/delete/toggle/test/events).
 */
export function WebhooksPage() {
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState('');
  const [eventTypes, setEventTypes] = useState<string[]>(FALLBACK_EVENT_TYPES);
  const [newUrl, setNewUrl] = useState('');
  const [newEvents, setNewEvents] = useState<string[]>(['pipeline.completed']);
  const [firingId, setFiringId] = useState<string | null>(null);
  const [lastFire, setLastFire] = useState<string | null>(null);

  const reload = async () => {
    try {
      const list = await webhookApi.list();
      setSubs(normalize(list));
      setNotice('');
    } catch {
      setNotice('无法连接后端 Webhook 服务');
    }
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const evRes = await webhookApi.listEvents();
        if (alive && Array.isArray(evRes.events) && evRes.events.length > 0) {
          setEventTypes(evRes.events);
          setNewEvents([evRes.events[0]]);
        }
      } catch { /* keep fallback events */ }
      if (!alive) return;
      await reload();
      if (alive) setLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  const toggleEvent = (ev: string) =>
    setNewEvents((es) => (es.includes(ev) ? es.filter((e) => e !== ev) : [...es, ev]));

  const subscribe = async () => {
    if (!newUrl.trim() || newEvents.length === 0) return;
    try {
      await webhookApi.register({ url: newUrl, events: newEvents });
      setNewUrl('');
      await reload();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setNotice(detail || '订阅失败（后端不可达或 URL 被安全策略拒绝）');
    }
  };

  const unsubscribe = async (id: string) => {
    try {
      await webhookApi.remove(id);
      setSubs((s) => s.filter((x) => x.id !== id));
    } catch {
      setNotice('删除失败：后端不可达');
    }
  };

  const toggleActive = async (s: Subscription) => {
    try {
      await webhookApi.toggle(s.id);
      setSubs((subs2) => subs2.map((x) => (x.id === s.id ? { ...x, active: !x.active } : x)));
    } catch {
      setNotice('切换状态失败：后端不可达');
    }
  };

  const fireTest = async (s: Subscription) => {
    setFiringId(s.id);
    setLastFire(null);
    try {
      const result = await webhookApi.test(s.id);
      if (result.success) {
        setLastFire(`webhook.test → ${s.url} (HTTP ${result.status_code})`);
      } else {
        setLastFire(`发送失败：${result.body || '未知错误'}`);
      }
      setSubs((subs2) => subs2.map((x) => (x.id === s.id ? { ...x, lastDelivery: new Date().toLocaleTimeString() } : x)));
    } catch {
      setLastFire(`发送失败：后端不可达 (${s.url})`);
    }
    setFiringId(null);
  };

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Integration / Webhooks" title="Webhook 设置"
        desc="订阅管线与渲染事件，推送到你的外部系统。支持测试触发以验证端点。" />

      {/* new subscription form */}
      <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 mb-6 max-w-[760px]">
        <p className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-3">
          <Plus className="w-3.5 h-3.5" /> 新增订阅
        </p>
        <div className="flex gap-2 mb-3">
          <input value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
            placeholder="https://your-endpoint.com/hook"
            className="flex-1 bg-surface rounded-cw-sm px-3 py-2 font-mono text-body-sm text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary placeholder:text-on-surface-variant/40" />
          <Button size="sm" onClick={subscribe} disabled={!newUrl.trim() || newEvents.length === 0}>订阅</Button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {eventTypes.map((ev) => (
            <button key={ev} onClick={() => toggleEvent(ev)}
              className={cn('px-2.5 py-1 rounded-cw-full font-mono text-caption border transition-all duration-short3 cursor-pointer',
                newEvents.includes(ev)
                  ? 'bg-primary/15 text-primary border-primary/40'
                  : 'bg-surface text-on-surface-variant border-outline-variant/40 hover:border-outline')}>
              {ev}
            </button>
          ))}
        </div>
      </div>

      {/* backend notice */}
      {notice && (
        <div className="flex items-center gap-2 bg-error/10 border border-error/30 rounded-cw-sm px-3.5 py-2 mb-4 max-w-[760px]">
          <span className="font-mono text-caption text-error">{notice}</span>
        </div>
      )}

      {/* last fire readout */}
      {lastFire && (
        <div className="flex items-center gap-2 bg-track-audio/10 border border-track-audio/30 rounded-cw-sm px-3.5 py-2 mb-4 max-w-[760px]">
          <Radio className="w-3.5 h-3.5 text-track-audio animate-pulse" />
          <span className="font-mono text-caption text-track-audio">{lastFire}</span>
        </div>
      )}

      {/* subscriptions list */}
      <div className="space-y-2.5 max-w-[760px]">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-16 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : subs.length === 0 ? (
          <div className="bg-surface-container border border-dashed border-outline-variant/40 rounded-cw-md p-10 text-center">
            <Webhook className="w-7 h-7 text-on-surface-variant/40 mx-auto mb-2" />
            <p className="text-body-sm text-on-surface-variant">暂无订阅</p>
          </div>
        ) : (
          subs.map((s) => (
            <div key={s.id} className="flex items-center gap-4 bg-surface-container border border-outline-variant/30 rounded-cw-md px-5 py-3.5
              hover:border-outline/60 transition-colors duration-short3 group">
              <span className={cn('w-9 h-9 rounded-cw-sm flex items-center justify-center shrink-0',
                s.active ? 'bg-primary/15 text-primary' : 'bg-surface-container-high text-on-surface-variant')}>
                <Webhook className="w-4.5 h-4.5" />
              </span>
              <div className="flex-1 min-w-0">
                <p className="font-mono text-body-sm text-on-surface truncate">{s.url}</p>
                <div className="flex items-center gap-1.5 mt-1">
                  {s.events.map((ev) => (
                    <span key={ev} className="font-mono text-caption px-1.5 py-px rounded-cw-xs bg-surface-container-high text-on-surface-variant border border-outline-variant/30">{ev}</span>
                  ))}
                  {s.lastDelivery && <span className="text-caption text-on-surface-variant/60 ml-1">上次 {s.lastDelivery}</span>}
                </div>
              </div>
              <button onClick={() => toggleActive(s)} title="启用/禁用" className="cursor-pointer">
                <StatusPill ok={s.active} label={s.active ? 'LIVE' : 'OFF'} />
              </button>
              <Button size="sm" variant="outline" onClick={() => fireTest(s)} disabled={firingId === s.id}>
                <Send className="w-3.5 h-3.5" /> {firingId === s.id ? '发送中' : '测试'}
              </Button>
              <button onClick={() => unsubscribe(s.id)}
                className="p-2 rounded-cw-xs text-on-surface-variant hover:text-error hover:bg-error/10 transition-colors cursor-pointer">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))
        )}
      </div>
    </ConsoleShell>
  );
}

function normalize(data: unknown): Subscription[] {
  if (Array.isArray(data)) {
    return data.map((d, i) => {
      const o = d as Record<string, unknown>;
      return {
        id: String(o.webhook_id ?? o.id ?? `sub_${i}`),
        url: String(o.url ?? ''),
        events: Array.isArray(o.events) ? (o.events as unknown[]).map(String) : [],
        active: Boolean(o.active ?? true),
      };
    });
  }
  return [];
}
