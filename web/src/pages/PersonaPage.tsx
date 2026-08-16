import { memo, useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { personaApi } from '@/services/api';
import { Button, Badge } from '@/components/ui';
import type { Persona } from '@/types/persona';
import {
  Plus, Sparkles, MessageSquare, Layers, BookOpen,
  Timer, ChevronRight, Dna,
} from 'lucide-react';

/** Demo personas (offline fallback) — rich parameter data for visualization. */
const DEMO_PERSONAS: Persona[] = [
  {
    persona_id: 'zamu_knowledge', persona_name: '扎姆·知识区', version: '2.3.1',
    parameter: {
      identity: { persona_id: 'zamu_knowledge', persona_name: '扎姆·知识区', version: '2.3.1', tone: 'critical', positioning: '批判型知识区 UP 主', knowledge_domains: ['数字文化', '科技哲学', '媒介批评'] },
      language: { max_sentence_length: 22, sentence_variance_target: 0.7, academic_density: 0.65, slang_ratio: 0.2 },
      rhythm: { cut_density_tier: 'medium', base_shot_duration_sec: 8, pause_frequency: 0.4 },
      visual: { color_palette: { primary: '#1a1a2e', accent: '#e94560' }, animation_style: '关键词标注', transition_weights: { hard_cut: 0.7, fade: 0.3 } },
      audio: { loudness_target_lufs: -16, bgm_slots: ['环境铺垫'] },
      constraints: { max_duration_sec: 900, source_citation_required: true },
    },
  },
  {
    persona_id: 'hexue_digital', persona_name: '何同学·数码', version: '1.8.0',
    parameter: {
      identity: { persona_id: 'hexue_digital', persona_name: '何同学·数码', version: '1.8.0', tone: 'creative', positioning: '创意型数码博主', knowledge_domains: ['消费电子', '摄影摄像', '极客文化'] },
      language: { max_sentence_length: 18, sentence_variance_target: 0.8, academic_density: 0.3, slang_ratio: 0.35 },
      rhythm: { cut_density_tier: 'high', base_shot_duration_sec: 4, pause_frequency: 0.25 },
      visual: { color_palette: { primary: '#0f0f1a', accent: '#00d4ff' }, animation_style: '流畅转场', transition_weights: { dissolve: 0.6, slide: 0.4 } },
      audio: { loudness_target_lufs: -14, bgm_slots: ['情绪引导'] },
      constraints: { max_duration_sec: 720 },
    },
  },
  {
    persona_id: 'vlog_daily', persona_name: '日常 Vlog 人格', version: '1.0.2',
    parameter: {
      identity: { persona_id: 'vlog_daily', persona_name: '日常 Vlog 人格', version: '1.0.2', tone: 'warm_storyteller', positioning: '温暖叙事型 Vlogger', knowledge_domains: ['生活方式', '旅行', '美食'] },
      language: { max_sentence_length: 25, sentence_variance_target: 0.6, academic_density: 0.1, slang_ratio: 0.4 },
      rhythm: { cut_density_tier: 'low', base_shot_duration_sec: 10, pause_frequency: 0.5 },
      visual: { color_palette: { primary: '#2d2418', accent: '#f59e0b' }, animation_style: '缓入缓出', transition_weights: { fade: 0.8 } },
      audio: { loudness_target_lufs: -18, bgm_slots: ['情绪引导'] },
      constraints: { max_duration_sec: 600 },
    },
  },
];

const TONE_COLORS: Record<string, string> = {
  critical: '#e94560', creative: '#00d4ff', warm_storyteller: '#f59e0b',
  tech_enthusiast: '#4F8CFF', industrial: '#A855F7',
};

/**
 * PersonaPage — the digital-persona gallery. Each card renders the persona's
 * four-layer identity and key parameters as a compact visual fingerprint.
 */
export function PersonaPage() {
  const navigate = useNavigate();
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [loading, setLoading] = useState(true);
  const [dataMode, setDataMode] = useState<'live' | 'demo'>('demo');

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await personaApi.list();
        if (alive) {
          setDataMode('live');
          setPersonas(Array.isArray(list) ? list : []);
        }
      } catch {
        if (alive) setPersonas(DEMO_PERSONAS);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <div className="h-full overflow-y-auto bg-surface">
      {/* header band */}
      <div className="relative border-b border-outline-variant/25 overflow-hidden">
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage: 'radial-gradient(circle at 20% 30%, #4F6BED 0%, transparent 50%), radial-gradient(circle at 85% 70%, #D1708E 0%, transparent 50%)',
        }} />
        <div className="relative max-w-[1100px] mx-auto px-8 py-8">
          <button onClick={() => navigate({ to: '/' })}
            className="text-label-sm text-on-surface-variant hover:text-primary transition-colors mb-4 cursor-pointer">
            ← 返回工作台
          </button>
          <div className="flex items-end justify-between flex-wrap gap-4">
            <div>
              <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-1.5">Persona System</p>
              <h1 className="font-display text-[34px] font-bold text-on-surface leading-tight">创作人格库</h1>
              <p className="text-body text-on-surface-variant mt-1.5 max-w-[520px]">
                人格是可训练、可迁移的创作者数字分身——从语言措辞到剪辑节奏，Agent 按它出初稿。
              </p>
            </div>
            <div className="flex items-center gap-2.5">
              <Badge variant={dataMode === 'live' ? 'success' : 'default'}>
                {dataMode === 'live' ? '实时数据' : '演示数据'}
              </Badge>
              <Button variant="outline" onClick={() => navigate({ to: '/persona/forge' })}>
                <MessageSquare className="w-4 h-4" /> 对话创建
              </Button>
              <Button onClick={() => navigate({ to: '/persona/forge' })}>
                <Sparkles className="w-4 h-4" /> 新建人格
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* persona grid */}
      <div className="max-w-[1100px] mx-auto px-8 py-8">
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-64 bg-surface-container rounded-cw-md animate-pulse" />
            ))}
          </div>
        ) : (
          <>
            {personas.length === 0 && dataMode === 'live' && (
              <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-6 text-center mb-4">
                <Layers className="w-8 h-8 text-on-surface-variant/40 mx-auto mb-2" />
                <p className="text-body-sm text-on-surface font-medium">还没有创建人格</p>
                <p className="text-label-sm text-on-surface-variant mt-1 mb-4">
                  通过对话或参数表单，打造属于你的创作人格。
                </p>
                <Button size="sm" onClick={() => navigate({ to: '/persona/forge' })}>
                  <Sparkles className="w-3.5 h-3.5" /> 新建人格
                </Button>
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {personas.map((p) => (
                <PersonaCard key={p.persona_id} persona={p}
                  onOpen={() => navigate({ to: '/persona/$personaId', params: { personaId: p.persona_id } })} />
              ))}
              {/* create tile */}
              <button
                onClick={() => navigate({ to: '/persona/forge' })}
                className="group min-h-[280px] rounded-cw-md border-2 border-dashed border-outline-variant/40 hover:border-primary/60
                  flex flex-col items-center justify-center gap-3 transition-all duration-short3 cursor-pointer
                  hover:bg-primary/5 text-on-surface-variant hover:text-primary"
              >
                <span className="w-12 h-12 rounded-cw-full border border-current flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Plus className="w-5 h-5" />
                </span>
                <span className="text-body-sm font-medium">创建新人格</span>
              <span className="text-caption opacity-70">描述风格 → 对话问答 → 生成</span>
            </button>
          </div>
          </>
        )}
      </div>
    </div>
  );
}

const PersonaCard = memo(function PersonaCardImpl({ persona, onOpen }: { persona: Persona; onOpen: () => void }) {
  const tone = persona.parameter.identity.tone;
  const accent = TONE_COLORS[tone] ?? '#4F8CFF';
  const rhythm = persona.parameter.rhythm;
  const lang = persona.parameter.language;
  const domains = persona.parameter.identity.knowledge_domains ?? [];

  // Build a small "rhythm fingerprint" bar series from cut density
  const densityMap: Record<string, number[]> = {
    low: [8, 6, 9, 7, 8, 6, 9, 7],
    medium: [6, 4, 7, 3, 6, 4, 7, 5],
    high: [3, 2, 4, 2, 3, 2, 4, 2],
    extreme: [2, 1, 2, 1, 2, 1, 2, 1],
  };
  const bars = densityMap[rhythm.cut_density_tier ?? 'medium'] ?? densityMap.medium;

  return (
    <button
      onClick={onOpen}
      className="group text-left bg-surface-container border border-outline-variant/30 rounded-cw-md overflow-hidden
        hover:border-primary/50 hover:-translate-y-1 hover:shadow-xl hover:shadow-primary/10
        transition-all duration-medium2 cursor-pointer"
    >
      {/* identity band */}
      <div className="relative px-4 pt-4 pb-3" style={{ background: `linear-gradient(135deg, ${accent}14, transparent 60%)` }}>
        <span className="absolute top-0 left-0 w-full h-[3px]" style={{ background: `linear-gradient(90deg, ${accent}, transparent)` }} />
        <div className="flex items-start justify-between">
          <div className="w-11 h-11 rounded-cw-md flex items-center justify-center shrink-0"
            style={{ background: `${accent}1F`, color: accent }}>
            <Dna className="w-5 h-5" />
          </div>
          <Badge variant="default" className="font-mono">v{persona.version}</Badge>
        </div>
        <h3 className="text-title-sm font-semibold text-on-surface mt-2.5 group-hover:text-primary transition-colors">
          {persona.persona_name}
        </h3>
        <p className="text-label-sm text-on-surface-variant mt-0.5">{persona.parameter.identity.positioning ?? tone ?? '未设置'}</p>
      </div>

      <div className="px-4 pb-4 space-y-3">
        {/* domains */}
        <div className="flex flex-wrap gap-1.5">
          {domains.slice(0, 3).map((d) => (
            <span key={d} className="px-2 py-0.5 rounded-cw-full text-caption bg-surface-container-high text-on-surface-variant border border-outline-variant/30">
              {d}
            </span>
          ))}
        </div>

        {/* rhythm fingerprint */}
        <div>
          <p className="flex items-center gap-1.5 text-caption text-on-surface-variant mb-1.5">
            <Timer className="w-3 h-3" /> 剪辑节奏 · {rhythm.cut_density_tier ?? 'medium'}
          </p>
          <div className="flex items-end gap-1 h-8">
            {bars.map((b, i) => (
              <span key={i} className="flex-1 rounded-t-[2px] transition-all duration-short3 group-hover:opacity-100 opacity-70"
                style={{ height: `${(b / 9) * 100}%`, background: accent }} />
            ))}
          </div>
        </div>

        {/* parameter meters */}
        <div className="space-y-1.5">
          <Meter label="学术密度" value={lang.academic_density ?? 0.5} color={accent} />
          <Meter label="口语化" value={lang.slang_ratio ?? 0.3} color={accent} />
        </div>

        {/* layers */}
        <div className="flex items-center justify-between pt-2 border-t border-outline-variant/20">
          <span className="flex items-center gap-1.5 text-caption text-on-surface-variant">
            <Layers className="w-3 h-3" /> 参数层
            <span className="opacity-50">·</span>
            <BookOpen className="w-3 h-3" /> 示例层
          </span>
          <ChevronRight className="w-4 h-4 text-on-surface-variant group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
        </div>
      </div>
    </button>
  );
});

function Meter({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-caption text-on-surface-variant w-14 shrink-0">{label}</span>
      <div className="flex-1 h-1 bg-surface rounded-cw-full overflow-hidden">
        <div className="h-full rounded-cw-full" style={{ width: `${value * 100}%`, background: color }} />
      </div>
      <span className="text-caption font-mono text-on-surface-variant w-7 text-right">{Math.round(value * 100)}</span>
    </div>
  );
}
