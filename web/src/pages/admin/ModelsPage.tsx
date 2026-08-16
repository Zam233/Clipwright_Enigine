import { useState } from 'react';
import { ConsoleShell, ConsoleHeading, StatusPill } from './ConsoleShell';
import { getApiClient } from '@/services/api';
import { Button } from '@/components/ui';
import { Cpu, Play, Loader2, Database, Layers } from 'lucide-react';

interface TestResult { status: 'idle' | 'running' | 'ok' | 'fail'; latencyMs?: number; detail?: string; }

/**
 * ModelsPage — LLM / Embedding / Rerank connectivity & latency test console.
 */
export function ModelsPage() {
  const [llm, setLlm] = useState<TestResult>({ status: 'idle' });
  const [embed, setEmbed] = useState<TestResult>({ status: 'idle' });
  const [rerank, setRerank] = useState<TestResult>({ status: 'idle' });
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);

  const runTest = async (
    endpoint: string,
    setter: (r: TestResult) => void,
    body?: Record<string, unknown>,
  ) => {
    setter({ status: 'running' });
    const t0 = performance.now();
    try {
      const { data } = await getApiClient().post(endpoint, body ?? { prompt: 'ping' });
      const latencyMs = Math.round(performance.now() - t0);
      setter({ status: 'ok', latencyMs, detail: summarize(data) });
    } catch (e: unknown) {
      const latencyMs = Math.round(performance.now() - t0);
      const msg = e instanceof Error ? e.message : 'request failed';
      setter({ status: 'fail', latencyMs, detail: msg });
    }
  };

  const loadConfig = async () => {
    try {
      const { data } = await getApiClient().get('/api/test/config');
      setConfig(data);
    } catch {
      setConfig({ note: '后端离线 — 以下为演示配置', llm: 'qwen-max', embed: 'bge-m3', rerank: 'bge-reranker' });
    }
  };

  const MODELS = [
    { key: 'llm', label: 'LLM 推理', desc: '大语言模型 · 脚本/结构/质检', icon: Cpu, endpoint: '/api/test/llm', state: llm, set: setLlm, color: '#4F8CFF', body: { prompt: '你好，请回复 ping' } },
    { key: 'embed', label: 'Embedding', desc: '向量化 · 素材语义检索', icon: Database, endpoint: '/api/test/embed', state: embed, set: setEmbed, color: '#A855F7', body: { text: '这是一个测试句子。' } },
    { key: 'rerank', label: 'Rerank', desc: '重排序 · 检索结果精排', icon: Layers, endpoint: '/api/test/rerank', state: rerank, set: setRerank, color: '#34D399', body: { query: '测试', candidates: ['选项A', '选项B', '选项C'] } },
  ];

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Diagnostics / Models" title="模型测试"
        desc="检测编排引擎所依赖的推理服务连通性与延迟。点击「测试」向对应端点发送探测请求。" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        {MODELS.map((m) => (
          <div key={m.key}
            className="relative bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 overflow-hidden
              hover:border-outline/60 transition-colors duration-short3 group">
            <span className="absolute top-0 left-0 w-full h-[3px]" style={{ background: `linear-gradient(90deg, ${m.color}, transparent)` }} />
            <div className="flex items-center justify-between mb-3">
              <span className="w-9 h-9 rounded-cw-sm flex items-center justify-center" style={{ background: `${m.color}1A`, color: m.color }}>
                <m.icon className="w-4.5 h-4.5" />
              </span>
              <StatusPill ok={m.state.status === 'ok'}
                label={m.state.status === 'ok' ? `${m.state.latencyMs}ms` : m.state.status === 'fail' ? 'FAIL' : m.state.status === 'running' ? 'TESTING' : 'IDLE'} />
            </div>
            <h3 className="text-body-sm font-semibold text-on-surface">{m.label}</h3>
            <p className="text-caption text-on-surface-variant mt-0.5 mb-3">{m.desc}</p>

            {m.state.detail && (
              <p className={`font-mono text-caption mb-3 truncate ${m.state.status === 'ok' ? 'text-track-audio' : 'text-error'}`}>
                {m.state.detail}
              </p>
            )}

            <Button size="sm" variant="outline" className="w-full"
              onClick={() => runTest(m.endpoint, m.set, m.body)} disabled={m.state.status === 'running'}>
              {m.state.status === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              {m.state.status === 'running' ? '探测中…' : '测试'}
            </Button>
          </div>
        ))}
      </div>

      {/* config readout */}
      <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2.5 bg-surface-container-high border-b border-outline-variant/20">
          <span className="font-mono text-label-sm text-on-surface-variant tracking-wider">MODEL_CONFIG</span>
          <Button size="sm" variant="ghost" onClick={loadConfig}>读取配置</Button>
        </div>
        <pre className="px-4 py-3 font-mono text-caption text-on-surface-variant leading-relaxed overflow-x-auto">
          {config ? JSON.stringify(config, null, 2) : '// 点击「读取配置」查看当前模型配置'}
        </pre>
      </div>
    </ConsoleShell>
  );
}

function summarize(data: unknown): string {
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>;
    if (typeof d.model === 'string') return `model=${d.model}`;
    if (typeof d.status === 'string') return `status=${d.status}`;
  }
  return 'ok';
}
