import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useProjectStore } from '@/stores/projectStore';
import { clearRequirementsDraft } from '@/stores/agentStore';
import { useBackendHealth } from './useBackendHealth';
import {
  healthApi, personaApi, projectApi, assetApi, typeMakerApi, pipelineApi,
  pluginApi,
  getApiClient,
} from '@/services/api';
import { accountApi } from '@/services/api/account';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { Button, Badge } from '@/components/ui';
import {
  Film, Settings, ArrowRight, Plus, Bot, ListChecks,
  PenLine, PackageCheck, Clock, Layers, Wand2, Mic, Image as ImageIcon,
  Upload, X, Check, Loader2, AudioLines,
  Scissors, FileText, FolderOpen, Clapperboard, Store, LayoutTemplate,
  Home, Shapes, User, Puzzle, Sparkles, Coins, Zap, LogOut, LogIn,
  Palette, Ruler, Server, ChevronRight, Gauge,
} from 'lucide-react';
import { ProjectCard, type ProjectCardData } from '@/components/shared/ProjectCard';
import { TemplateGallery } from '@/components/shared/TemplateGallery';
import { fmtDur, relTime, uid } from '@/lib/utils';
import { toast } from '@/stores/toastStore';

/* ── 图一：左侧导航 tab ── */
type HomeTab = 'home' | 'types' | 'personas' | 'plugins' | 'create' | 'settings';

/* ── types ─────────────────────────────────────────────── */
interface PersonaOpt { id: string; name: string; tone: string }
interface PluginOpt { id: string; name: string; desc: string; color: string }
interface ProjectOpt {
  id: string; name: string; type: string; duration: string;
  tracks: number; edited: string; grad: [string, string]; featured?: boolean;
  thumbnail?: string;
}
interface DubAudio { path: string; name: string; duration: number }
interface MatSource { id: string; name: string }

/* ── offline demo fallbacks ────────────────────────────── */
const DEMO_PERSONAS: PersonaOpt[] = [
  { id: 'default', name: '默认人格', tone: '通用' },
  { id: 'zamu_knowledge', name: '扎姆·知识区', tone: '批判型' },
  { id: 'hexue_digital', name: '何同学·数码', tone: '创意型' },
  { id: 'yingshi_industrial', name: '影视飓风·工业', tone: '工业型' },
];

const PLUGIN_PALETTE = ['#4F8CFF', '#FF6B6B', '#A855F7', '#34D399', '#FBBF24', '#F59E0B'];
const DEMO_PLUGINS: PluginOpt[] = [
  { id: 'knowledge_longform', name: '知识区长片', desc: '5-15s 镜头 · 硬切', color: '#4F8CFF' },
  { id: 'kichiku_fastcut', name: '鬼畜快剪', desc: '0.3-2s · 闪白', color: '#FF6B6B' },
  { id: 'digital_review', name: '数码评测', desc: '3-8s · 缓入缓出', color: '#A855F7' },
  { id: 'vlog_daily', name: 'Vlog 日常', desc: '3-10s · 混合', color: '#34D399' },
];

const PROJECT_GRADS: [string, string][] = [
  ['#A855F7', '#4F8CFF'], ['#4F8CFF', '#34D399'], ['#FF6B6B', '#FBBF24'], ['#34D399', '#F59E0B'],
];

const WORKFLOW = [
  { icon: ListChecks, title: '需求规划', desc: '需求 Agent 梳理创意简报与制作规划书', color: '#4F8CFF' },
  { icon: Bot, title: 'Agent 生成初稿', desc: '六 Agent 管线自动产出多轨时间线', color: '#A855F7' },
  { icon: PenLine, title: '人在时间轴审阅', desc: '逐帧微调，不满意可让 Agent 局部重做', color: '#FBBF24', highlight: true },
  { icon: PackageCheck, title: '渲染导出', desc: '一键导出 B 站 / 抖音 / YouTube 预设', color: '#34D399' },
];

/* ── helpers ───────────────────────────────────────────── */
export function splitScriptToCaptions(text: string, mode: 'period' | 'punctuation'): string[] {
  const t = text.trim();
  if (!t) return [];
  if (mode === 'punctuation') {
    const parts: string[] = [];
    let buf = '';
    for (const ch of t) {
      if ('，。！；？：'.includes(ch)) {
        if ('？！'.includes(ch)) parts.push((buf + ch).trim());
        else if (buf.trim()) parts.push(buf.trim());
        buf = '';
      } else buf += ch;
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts.filter(Boolean);
  }
  const parts = t.split(/[。！？.!?]/).map((s) => s.trim()).filter(Boolean);
  return parts.length ? parts : [t];
}

/** 客户端探测音频文件真实时长（秒）。失败返回 0。 */
function detectAudioDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = new Audio();
    audio.preload = 'metadata';
    const cleanup = () => URL.revokeObjectURL(url);
    audio.onloadedmetadata = () => {
      const d = audio.duration;
      cleanup();
      resolve(Number.isFinite(d) && d > 0 ? d : 0);
    };
    audio.onerror = () => { cleanup(); resolve(0); };
    audio.src = url;
  });
}

/* ── page ──────────────────────────────────────────────── */
export function HomePage() {
  const navigate = useNavigate();

  // 图一：左侧导航 tab
  const [activeTab, setActiveTab] = useState<HomeTab>('home');
  // CREADIT：用户余额 + 本次创作预估
  const authUser = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const [credit, setCredit] = useState<number | null>(null);
  const [creditEstimate, setCreditEstimate] = useState<number | null>(null);

  // 加载 CREADIT 余额（登录态）
  useEffect(() => {
    if (!accessToken) return;
    let alive = true;
    accountApi.creditBalance(accessToken)
      .then((r) => { if (alive) setCredit(r.credit); })
      .catch(() => {});
    return () => { alive = false; };
  }, [accessToken]);

  const [guardNotice, setGuardNotice] = useState<string | null>(null);
  const backend = useBackendHealth();
  const [dataMode, setDataMode] = useState<'live' | 'demo'>('demo');
  const [personas, setPersonas] = useState<PersonaOpt[]>(DEMO_PERSONAS);
  const [plugins, setPlugins] = useState<PluginOpt[]>(DEMO_PLUGINS);
  // BUG3: 真实第三方插件（/api/plugin/list，区别于类型插件 typeMakerApi）
  const [extPlugins, setExtPlugins] = useState<PluginOpt[]>([]);
  const [projects, setProjects] = useState<ProjectOpt[]>([]);

  const [topic, setTopic] = useState('');
  const [script, setScript] = useState('');
  const [personaId, setPersonaId] = useState('');
  const [pluginId, setPluginId] = useState('knowledge_longform');
  const [mode, setMode] = useState<'voiceover' | 'visual'>('voiceover');
  const [splitMode, setSplitMode] = useState<'period' | 'punctuation'>('period');
  const [audio, setAudio] = useState<DubAudio | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchErr, setLaunchErr] = useState<string | null>(null);
  // A3: 模板画廊弹层
  const [templateGalleryOpen, setTemplateGalleryOpen] = useState(false);
  // P8: dry-run 预览模式（只生成粗剪时间线）
  const [dryRun, setDryRun] = useState(false);
  const [materialSources, setMaterialSources] = useState<MatSource[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const audioInputRef = useRef<HTMLInputElement>(null);
  // G6: 文稿智能预判（predict-script）——长文稿且后端在线时显示推荐卡片
  const [prediction, setPrediction] = useState<{
    video_type?: string;
    estimated_duration_sec?: number;
    recommended_persona_tone?: string;
    summary?: string;
  } | null>(null);

  // G6: 防抖调用 predict-script（800ms），失败静默（不影响启动）
  useEffect(() => {
    if (backend !== 'online' || script.trim().length < 50) {
      setPrediction(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await pipelineApi.predictScript(script.trim());
        if (res && typeof res === 'object') setPrediction(res as typeof prediction);
      } catch { /* 静默失败 */ }
    }, 800);
    return () => clearTimeout(timer);
  }, [script, backend]);

  useEffect(() => {
    let alive = true;
    // Check for guard notice from router redirect
    const notice = sessionStorage.getItem('cw_guard_notice');
    if (notice) {
      sessionStorage.removeItem('cw_guard_notice');
      queueMicrotask(() => alive && setGuardNotice(notice));
    }

    (async () => {
      try {
        const [ps, projs] = await Promise.all([
          personaApi.list(),
          projectApi.list(),
        ]);
        if (!alive) return;
        setDataMode('live');
        if (Array.isArray(ps) && ps.length) {
          setPersonas(ps.map((p) => ({
            id: p.persona_id,
            name: p.persona_name,
            tone: p.parameter?.identity?.tone ?? '通用',
          })));
        }
        if (Array.isArray(projs)) {
          // 过滤无 id 的脏记录（历史遗留），避免 React duplicate-key 警告
          const valid = projs.filter((pr) => pr && pr.id);
          setProjects(valid.length > 0
              ? valid.map((pr, i) => ({
                id: pr.id,
                name: pr.name,
                type: pr.plugin_id ?? '—',
                duration: fmtDur(pr.duration_sec ?? 0),
                tracks: pr.track_count ?? 0,
                edited: relTime(pr.updated_at),
                grad: PROJECT_GRADS[i % PROJECT_GRADS.length],
                featured: i === 0,
                thumbnail: pr.has_thumbnail ? projectApi.getThumbnailUrl(pr.id, pr.updated_at) : undefined,
              }))
            : []);
        }
      } catch {
        if (alive) setDataMode('demo');
      }
    })();

    const loadPlugins = async () => {
      try {
        const data = await typeMakerApi.list();
        if (Array.isArray(data) && data.length > 0) {
          return data.map(
            (t: { id: string; name: string; description?: string }, i: number) => ({
              id: t.id,
              name: t.name || t.id,
              desc: t.description || '',
              color: PLUGIN_PALETTE[i % PLUGIN_PALETTE.length],
            }),
          );
        }
      } catch {
        /* types not available */
      }
      return null;
    };

    (async () => {
      const result = await loadPlugins();
      if (alive && result) setPlugins(result);
    })();

    // BUG3: 真实第三方插件（/api/plugin/list → manifest 列表）
    (async () => {
      try {
        const data = await pluginApi.list();
        if (!alive || !Array.isArray(data)) return;
        const items = data
          .map((p: Record<string, unknown>, i: number) => {
            const m = (p.manifest ?? p) as Record<string, unknown>;
            return {
              id: String(m.id ?? p.id ?? ''),
              name: String(m.name ?? p.id ?? ''),
              desc: String(m.description ?? m.kind ?? 'capability'),
              color: PLUGIN_PALETTE[i % PLUGIN_PALETTE.length],
            };
          })
          .filter((x) => x.id);
        if (items.length > 0) setExtPlugins(items);
      } catch {
        /* 后端离线：保持空列表 */
      }
    })();

    assetApi.listSources()
      .then((srcs) => { if (alive && Array.isArray(srcs)) setMaterialSources(srcs); })
      .catch(() => {});

    return () => { alive = false; };
  }, []);

  const captions = useMemo(
    () => splitScriptToCaptions(script, splitMode),
    [script, splitMode],
  );

  const estDuration = useMemo(() => {
    if (audio?.duration) return audio.duration;
    if (script.trim()) return Math.max(30, Math.min(1800, script.trim().length / 5));
    return 60;
  }, [audio, script]);

  const stepDone = {
    script: Boolean(topic.trim() || script.trim()),
    style: Boolean(personaId || pluginId),
    dub: mode === 'visual' ? true : Boolean(audio),
  };

  // 图一：每次表单变化时重新预估 CREADIT 消耗（登录态）
  useEffect(() => {
    if (!accessToken) return;
    let alive = true;
    const timer = setTimeout(() => {
      accountApi.creditEstimate({
        pipeline: true,
        dry_run: dryRun,
        voice: mode === 'voiceover' && !!audio,
        asset_count: selectedSources.length > 0 ? 1 : 0,
      }, accessToken)
        .then((r) => { if (alive) setCreditEstimate(r.total); })
        .catch(() => {});
    }, 500);
    return () => { alive = false; clearTimeout(timer); };
  }, [accessToken, dryRun, mode, audio, selectedSources]);

  // G6: 一键填入预判推荐（类型插件 / 时长）。仅映射已知插件；时长覆盖当前估算（无真实音频时）。
  const applyPrediction = () => {
    if (!prediction) return;
    if (prediction.video_type) {
      const type = prediction.video_type.toLowerCase();
      const mapping: Record<string, string> = {
        'knowledge': 'knowledge_longform',
        '知识': 'knowledge_longform',
        'long': 'knowledge_longform',
        'fastcut': 'kichiku_fastcut',
        '鬼畜': 'kichiku_fastcut',
        'review': 'digital_review',
        '评测': 'digital_review',
        'vlog': 'vlog_daily',
      };
      for (const [key, pid] of Object.entries(mapping)) {
        if (type.includes(key)) { setPluginId(pid); break; }
      }
    }
    setPrediction(null);
  };

  const pickAudio = async (file: File) => {    setUploading(true);
    setUploadErr(null);
    try {
      // 客户端检测真实音频时长（不依赖后端 ffprobe——Windows 常缺 ffprobe 会返回 0，
      // 导致时长退化为文案估算值，进而使时间轴远短于配音实际长度）
      const clientDuration = await detectAudioDuration(file);
      const res = await assetApi.upload(file);
      const path = res.file_path ?? res.path ?? '';
      const duration = clientDuration || res.duration_sec || 0;
      setAudio({ path, name: res.filename ?? file.name, duration });
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (e instanceof Error ? e.message : '上传失败');
      setUploadErr(detail);
    } finally {
      setUploading(false);
    }
  };

  const launch = async () => {
    if (!topic.trim() && !script.trim()) {
      setLaunchErr('请填写选题或文案');
      return;
    }
    setLaunching(true);
    setLaunchErr(null);
    const st = useProjectStore.getState();
    st.setProjectName(topic.trim() || '未命名项目');
    st.setPersonaId(personaId || 'default');
    st.setPluginId(pluginId);
    st.setRequirementsTopic(topic.trim());
    st.setRequirementsScript(script.trim());
    st.setRequirementsAudioDuration(estDuration);
    st.setScriptText(script.trim());
    st.setVideoMode(mode);
    st.setSplitMode(splitMode);
    st.setAudioPath(audio?.path || '');
    st.setAudioDurationSec(audio?.duration || 0);
    st.setMaterialSourceIds(selectedSources);
    st.setDryRun(dryRun); // P8: 传递 dry-run 预览模式
    try {
      const prefs = JSON.parse(localStorage.getItem('clipwright_voice_prefs') || '{}');
      if (prefs.voiceId) st.setVoiceId(prefs.voiceId);
      if (typeof prefs.autoDub === 'boolean') st.setAutoDub(prefs.autoDub);
    } catch { /* ignore */ }

    // Create project on backend
    // 若健康检查尚未完成（'checking'），先显式探测一次，避免误判为离线
    if (backend === 'online' || backend === 'checking') {
      let online = backend === 'online';
      if (!online) {
        try { await healthApi.check(); online = true; } catch { online = false; }
      }
      if (online) {
        try {
          const project = await projectApi.create({
            name: topic.trim() || '未命名项目',
            timeline: null,
            persona_id: personaId || undefined,
            plugin_id: pluginId || undefined,
          });
          st.setProjectId(project.id);
          // 仅在项目创建成功后清除旧 draft，失败则保留草稿
          clearRequirementsDraft();
          navigate({ to: '/editor/$projectId', params: { projectId: project.id } });
          return;
        } catch {
          // Fall through — offline
        }
      }
    }
    // Offline / backend create failed → open a local empty project (demo mode)
    const localId = uid('proj');
    st.setProjectId(localId);
    clearRequirementsDraft();
    navigate({ to: '/editor/$projectId', params: { projectId: localId } });
  };

  const openBlank = async () => {
    setLaunching(true);
    const st = useProjectStore.getState();
    st.setProjectName('未命名项目');
    if (backend === 'online' || backend === 'checking') {
      let online = backend === 'online';
      if (!online) {
        try { await healthApi.check(); online = true; } catch { online = false; }
      }
      if (online) {
        try {
          const project = await projectApi.create({ name: '未命名项目', timeline: null });
          st.setProjectId(project.id);
          navigate({ to: '/editor/$projectId', params: { projectId: project.id } });
          return;
        } catch { /* fall through */ }
      }
    }
    // Offline → open a local empty project (demo mode)
    const localId = uid('proj');
    st.setProjectId(localId);
    navigate({ to: '/editor/$projectId', params: { projectId: localId } });
  };

  const openProject = async (proj: ProjectOpt) => {
    navigate({ to: '/editor/$projectId', params: { projectId: proj.id } });
  };

  const handleDeleteProject = async (projectId: string) => {
    try {
      await projectApi.remove(projectId);
      setProjects((prev) => prev.filter((project) => project.id !== projectId));
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      toast(`删除失败：${reason}，项目已保留`, 'error');
    }
  };

  const reloadProjects = async () => {
    try {
      const projs = await projectApi.list();
      if (Array.isArray(projs)) {
        const valid = projs.filter((pr) => pr && pr.id);
        setProjects(valid.map((pr, i) => ({
          id: pr.id,
          name: pr.name,
          type: pr.plugin_id ?? '—',
          duration: fmtDur(pr.duration_sec ?? 0),
          tracks: pr.track_count ?? 0,
          edited: relTime(pr.updated_at),
          grad: PROJECT_GRADS[i % PROJECT_GRADS.length],
          featured: i === 0,
          thumbnail: pr.has_thumbnail ? projectApi.getThumbnailUrl(pr.id, pr.updated_at) : undefined,
        })));
      }
    } catch { /* offline */ }
  };

  const handleDuplicateProject = async (project: ProjectOpt) => {
    try {
      await projectApi.duplicate(project.id);
      await reloadProjects();
    } catch {
      toast('复制项目失败 — 后端不可达', 'error');
    }
  };

  return (
    <div className="relative min-h-full h-full overflow-y-auto bg-surface text-on-surface">
      <Backdrop />

      <div className="relative z-10 flex min-h-full">
        {/* ── 图一：左侧栏（用户卡片 + 导航） ── */}
        <HomeSidebar
          activeTab={activeTab}
          onTab={setActiveTab}
          credit={credit}
          user={authUser}
          onTopup={async () => {
            try {
              const r = await accountApi.creditTopup(100, accessToken ?? undefined);
              setCredit(r.credit);
              toast('CREADIT +100（测试充值）');
            } catch {
              toast('充值失败：Server 不可达', 'error');
            }
          }}
          estimate={creditEstimate}
        />

        <main className="flex-1 min-w-0 w-full px-8 pb-10">
          {guardNotice && (
            <div className="mb-4 flex items-center gap-3 rounded-cw-md bg-amber-500/10 border border-amber-500/30 px-4 py-3 text-amber-200 text-body-sm">
              <span className="flex-1">{guardNotice}</span>
              <button
                onClick={() => setGuardNotice(null)}
                className="ml-2 text-amber-200/60 hover:text-amber-200 transition-colors cursor-pointer"
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
          )}

          {/* ── 首页 tab：开始创作 + 空项目 + 最近项目（图一） ── */}
          {activeTab === 'home' && (
            <section className="pt-8 max-w-[1080px]">
              <div className="flex items-end justify-between gap-4 mb-8">
                <div>
                  <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-2">
                    ClipWright · AI 辅助视频创作
                  </p>
                  <h1 className="font-display text-[40px] leading-[1.15] font-bold text-on-surface mb-1">
                    把你的选题，
                    <span className="text-primary">剪</span>成一支视频。
                  </h1>
                  <p className="text-body text-on-surface-variant mt-1 max-w-[520px]">
                    稿件 → 风格 → 配音 → 启动。Agent 负责结构化与体力活，你负责审美与微调。
                  </p>
                </div>
                <Badge variant={dataMode === 'live' ? 'success' : 'default'}>
                  {dataMode === 'live' ? '实时数据' : '演示数据'}
                </Badge>
              </div>

              {/* 顶部操作入口：开始创作 / 空项目 */}
              <div className="flex flex-wrap items-center gap-3 mb-10">
                <Button size="lg" onClick={() => { setActiveTab('create'); window.scrollTo({ top: 0 }); }} className="min-w-[220px] group">
                  <Sparkles className="w-4.5 h-4.5" /> 开始创作
                  <ArrowRight className="w-4 h-4 transition-transform duration-short3 group-hover:translate-x-1" />
                </Button>
                <Button size="lg" variant="outline" onClick={openBlank} className="min-w-[160px]">
                  <Scissors className="w-4 h-4" /> 空项目
                </Button>
                <Button size="lg" variant="ghost" onClick={() => setTemplateGalleryOpen(true)}
                  className="text-on-surface-variant hover:text-primary">
                  <LayoutTemplate className="w-4 h-4" /> 从模板开始
                </Button>
                {credit !== null && (
                  <span className="ml-auto flex items-center gap-1.5 font-mono text-caption text-on-surface-variant bg-surface-container border border-outline-variant/30 rounded-cw-full px-3 py-1.5">
                    <Coins className="w-3.5 h-3.5 text-track-text" />
                    本次预估消耗 {creditEstimate ?? '—'} CREADIT
                  </span>
                )}
              </div>

              {/* 最近项目（图一：缩略卡片横向） */}
              <div className="flex items-end justify-between mb-4">
                <div>
                  <p className="font-mono text-label-sm tracking-[0.25em] text-on-surface-variant uppercase">Recent</p>
                  <h2 className="text-title font-semibold text-on-surface mt-0.5">最近项目</h2>
                </div>
                <button
                  onClick={() => navigate({ to: '/projects' })}
                  className="flex items-center gap-1.5 text-label-sm text-primary hover:text-primary/80 transition-colors cursor-pointer"
                >
                  全部项目 <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>

              <div className="grid grid-cols-12 gap-4">
                {projects.map((proj) => (
                  <ProjectCard
                    key={proj.id}
                    proj={proj}
                    mode="simple"
                    onOpen={() => openProject(proj)}
                    onDelete={() => handleDeleteProject(proj.id)}
                    onDuplicate={() => handleDuplicateProject(proj)}
                  />
                ))}
                {projects.length === 0 && (
                  <div className="col-span-12 flex flex-col items-center justify-center py-12 text-center gap-2
                    border border-dashed border-outline-variant/30 rounded-cw-md">
                    <FolderOpen className="w-8 h-8 text-on-surface-variant/40" />
                    <p className="text-body-sm text-on-surface-variant">
                      {dataMode === 'demo' ? '演示数据 · 暂无后端项目' : '还没有项目'}
                    </p>
                    <p className="text-caption text-on-surface-variant/60">
                      {dataMode === 'demo'
                        ? '后端未连接，当前展示的是演示数据；连接后端后这里会显示你的真实项目'
                        : '点击「开始创作」创建你的第一个视频'}
                    </p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* ── 类型 tab ── */}
          {activeTab === 'types' && (
            <section className="pt-8 max-w-[1080px]">
              <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-2">Video Types</p>
              <h1 className="font-display text-[32px] font-bold text-on-surface mb-6">视频类型</h1>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                {plugins.map((pl) => (
                  <button key={pl.id}
                    onClick={() => { setPluginId(pl.id); setActiveTab('create'); }}
                    className="text-left bg-surface-container border border-outline-variant/30 rounded-cw-md p-4
                      hover:border-primary/50 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/10
                      transition-all duration-short3 cursor-pointer group">
                    <span className="flex items-center gap-2.5 mb-2">
                      <i className="w-3 h-3 rounded-full shrink-0" style={{ background: pl.color }} />
                      <span className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">{pl.name}</span>
                    </span>
                    {pl.desc && <span className="block text-caption text-on-surface-variant/70 font-mono">{pl.desc}</span>}
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* ── 人格 tab ── */}
          {activeTab === 'personas' && (
            <section className="pt-8 max-w-[1080px]">
              <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-2">Persona</p>
              <h1 className="font-display text-[32px] font-bold text-on-surface mb-6">创作人格</h1>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                {personas.map((p) => (
                  <button key={p.id}
                    onClick={() => { setPersonaId(p.id); setActiveTab('create'); }}
                    className={`text-left bg-surface-container border rounded-cw-md p-4 transition-all duration-short3 cursor-pointer group ${
                      personaId === p.id
                        ? 'border-primary bg-primary/5 shadow-lg shadow-primary/10'
                        : 'border-outline-variant/30 hover:border-primary/50 hover:-translate-y-0.5'
                    }`}>
                    <span className="w-9 h-9 rounded-cw-sm bg-primary-container flex items-center justify-center mb-2.5">
                      <User className="w-4.5 h-4.5 text-on-primary-container" />
                    </span>
                    <p className="text-body-sm font-semibold text-on-surface group-hover:text-primary transition-colors">{p.name}</p>
                    <p className="text-caption text-on-surface-variant mt-0.5">{p.tone}</p>
                  </button>
                ))}
                <button
                  onClick={() => navigate({ to: '/persona/forge' })}
                  className="min-h-[120px] rounded-cw-md border-2 border-dashed border-outline-variant/40 hover:border-primary/60
                    flex flex-col items-center justify-center gap-2 text-on-surface-variant hover:text-primary
                    transition-all duration-short3 cursor-pointer">
                  <Plus className="w-5 h-5" /> 新建人格
                </button>
              </div>
            </section>
          )}

          {/* ── 插件 tab ── */}
          {activeTab === 'plugins' && (
            <section className="pt-8 max-w-[1080px]">
              <div className="flex items-center justify-between mb-6">
                <div>
                  <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-2">Extensions</p>
                  <h1 className="font-display text-[32px] font-bold text-on-surface">插件</h1>
                </div>
                <Button size="sm" variant="outline" onClick={() => navigate({ to: '/market' })}>
                  <Store className="w-3.5 h-3.5" /> 插件市场
                </Button>
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                {extPlugins.length === 0 ? (
                  <div className="col-span-full flex flex-col items-center justify-center py-12 text-center gap-2
                    border border-dashed border-outline-variant/30 rounded-cw-md">
                    <Puzzle className="w-8 h-8 text-on-surface-variant/40" />
                    <p className="text-body-sm text-on-surface-variant">暂无已加载插件</p>
                    <p className="text-caption text-on-surface-variant/60">
                      后端未连接或插件目录为空；可在「插件市场」浏览并安装。
                    </p>
                  </div>
                ) : (
                  extPlugins.map((pl) => (
                    <div key={pl.id} className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4
                      hover:border-primary/50 transition-all duration-short3">
                      <span className="flex items-center gap-2.5 mb-2">
                        <span className="w-8 h-8 rounded-cw-sm flex items-center justify-center" style={{ background: `${pl.color}1A`, color: pl.color }}>
                          <Puzzle className="w-4 h-4" />
                        </span>
                        <span className="text-body-sm font-semibold text-on-surface">{pl.name}</span>
                      </span>
                      {pl.desc && <span className="block text-caption text-on-surface-variant/70 font-mono">{pl.desc}</span>}
                    </div>
                  ))
                )}
              </div>
            </section>
          )}

          {/* ── 设置 tab：系统设置（主题/语言/API/时间轴默认） ── */}
          {activeTab === 'settings' && (
            <section className="pt-8 max-w-[1080px]">
              <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-2">Preferences</p>
              <h1 className="font-display text-[32px] font-bold text-on-surface mb-6">设置</h1>
              <HomeSettings onAdvanced={() => navigate({ to: '/settings' })} />
            </section>
          )}

          {/* ── 创作 tab：图二创作控制台（由「开始创作」进入） ── */}
          {activeTab === 'create' && (
          <section className="grid grid-cols-12 gap-6 pt-8">
            {/* ── production console ── */}
            <div className="col-span-12 lg:col-span-7">
              <p className="font-mono text-label-sm tracking-[0.3em] text-primary uppercase mb-3">
                ClipWright · AI 辅助视频创作
              </p>
              <h1 className="font-display text-[44px] leading-[1.15] font-bold text-on-surface mb-2">
                把你的选题，
                <span className="text-primary">剪</span>成一支视频。
              </h1>
              <p className="text-body text-on-surface-variant mb-7 max-w-[480px]">
                稿件 → 风格 → 配音 → 启动。Agent 负责结构化与体力活，你负责审美与微调。
              </p>

              <div className="bg-surface-container border border-outline-variant/40 rounded-cw-lg overflow-hidden shadow-2xl shadow-black/40">
                {/* console header — Material You tonal banner */}
                <div className="relative flex items-center gap-3.5 px-5 py-4 bg-surface-container-high border-b border-outline-variant/30 overflow-hidden">
                  {/* ambient tonal wash */}
                  <div
                    className="absolute inset-0 pointer-events-none"
                    style={{ background: 'linear-gradient(115deg, var(--cw-primary-container) 0%, transparent 60%)', opacity: 0.3 }}
                  />
                  {/* leading tonal icon */}
                  <span className="relative w-11 h-11 rounded-cw-md bg-primary-container flex items-center justify-center shrink-0 shadow-md shadow-primary/25 ring-1 ring-on-primary-container/15">
                    <Clapperboard className="w-5 h-5 text-on-primary-container" />
                  </span>
                  <div className="relative flex-1 min-w-0">
                    <p className="text-title-sm font-semibold text-on-surface leading-snug">新建制作</p>
                    <p className="font-mono text-caption text-on-surface-variant/80 tracking-[0.08em]">new_production.session</p>
                  </div>
                  <Badge variant={dataMode === 'live' ? 'success' : 'warning'} className="relative shrink-0">
                    {dataMode === 'live' ? '实时数据' : '演示数据'}
                  </Badge>
                </div>

                <div className="p-5 space-y-6">
                  {/* ── 01 稿件 ── */}
                  <ConsoleStep
                    no="01" title="稿件" en="SCRIPT" done={stepDone.script}
                    stat={script.trim()
                      ? `${script.trim().length} 字 · 预估 ${fmtDur(estDuration)}`
                      : undefined}
                  >
                    <div className="flex items-center gap-2 bg-surface rounded-cw-sm border border-outline-variant/40 focus-within:border-primary transition-colors px-3 mb-2.5">
                      <span className="font-mono text-primary text-body select-none">&gt;</span>
                      <input
                        value={topic}
                        onChange={(e) => setTopic(e.target.value)}
                        placeholder="视频选题，例如：深度解析某品牌新手机的散热设计…"
                        className="flex-1 bg-transparent outline-none py-3 text-body text-on-surface placeholder:text-on-surface-variant/40 font-mono"
                      />
                      {topic && (
                        <button onClick={() => setTopic('')} className="p-1 text-on-surface-variant/50 hover:text-on-surface cursor-pointer">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                    <textarea
                      value={script}
                      onChange={(e) => setScript(e.target.value)}
                      rows={4}
                      placeholder="粘贴口播文案 / 旁白稿（可选）。将按所选规则切分为字幕段…"
                      className="w-full bg-surface rounded-cw-sm border border-outline-variant/40 focus:border-primary transition-colors
                        px-3 py-2.5 text-body-sm text-on-surface leading-relaxed outline-none resize-y placeholder:text-on-surface-variant/40"
                    />

                    {/* G6: 文稿智能预判推荐卡片 */}
                    {prediction && (
                      <div className="mt-2.5 bg-primary/5 border border-primary/30 rounded-cw-md p-3">
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <Wand2 className="w-3.5 h-3.5 text-primary" />
                          <span className="text-label font-medium text-on-surface">智能预判</span>
                          <span className="ml-auto text-caption text-on-surface-variant/60">基于文稿分析</span>
                        </div>
                        {prediction.video_type && (
                          <p className="text-label-sm text-on-surface"><span className="text-on-surface-variant">推荐类型：</span>{prediction.video_type}</p>
                        )}
                        {prediction.estimated_duration_sec && (
                          <p className="text-label-sm text-on-surface"><span className="text-on-surface-variant">预估时长：</span>约 {prediction.estimated_duration_sec}s</p>
                        )}
                        {prediction.recommended_persona_tone && (
                          <p className="text-label-sm text-on-surface"><span className="text-on-surface-variant">风格基调：</span>{prediction.recommended_persona_tone}</p>
                        )}
                        {prediction.summary && (
                          <p className="text-caption text-on-surface-variant mt-1">{prediction.summary}</p>
                        )}
                        <button
                          onClick={applyPrediction}
                          className="mt-2 px-2.5 py-1 rounded-cw-xs bg-primary text-on-primary text-label-sm hover:bg-primary/90 transition-colors cursor-pointer"
                        >
                          一键填入推荐类型
                        </button>
                      </div>
                    )}
                  </ConsoleStep>

                  {/* ── 02 风格 ── */}
                  <ConsoleStep no="02" title="风格" en="STYLE" done={stepDone.style}>
                    <label className="text-label text-on-surface-variant block mb-2">创作人格 Persona</label>
                    <div className="flex flex-wrap gap-2 mb-4">
                      {personas.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => setPersonaId(p.id)}
                          className={`px-3 py-1.5 rounded-cw-full text-label-sm border transition-all duration-short3 cursor-pointer ${
                            personaId === p.id
                              ? 'bg-primary-container border-primary text-on-primary-container shadow-md shadow-primary/20'
                              : 'bg-surface-container-high border-outline-variant/40 text-on-surface-variant hover:border-outline hover:text-on-surface'
                          }`}
                        >
                          {p.name}
                          <span className="opacity-60 ml-1">· {p.tone}</span>
                        </button>
                      ))}
                      <button
                        onClick={() => navigate({ to: '/persona/forge' })}
                        className="w-8 h-8 rounded-cw-full border border-dashed border-outline-variant/40 hover:border-primary/60
                          flex items-center justify-center text-on-surface-variant hover:text-primary
                          bg-surface-container-high hover:bg-primary/5 transition-all cursor-pointer"
                        title="新建人格"
                      >
                        <Plus className="w-4 h-4" />
                      </button>
                    </div>
                    <label className="text-label text-on-surface-variant block mb-2">视频类型插件</label>
                    <div className="grid grid-cols-2 gap-2">
                      {plugins.map((pl) => (
                        <button
                          key={pl.id}
                          onClick={() => setPluginId(pl.id)}
                          className={`text-left px-3 py-2.5 rounded-cw-sm border transition-all duration-short3 cursor-pointer group ${
                            pluginId === pl.id
                              ? 'border-primary bg-primary/8 shadow-md shadow-primary/10'
                              : 'border-outline-variant/40 bg-surface-container-high hover:border-outline'
                          }`}
                        >
                          <span className="flex items-center gap-2">
                            <i className="w-2 h-2 rounded-full shrink-0" style={{ background: pl.color }} />
                            <span className={`text-body-sm font-medium truncate ${pluginId === pl.id ? 'text-on-surface' : 'text-on-surface-variant group-hover:text-on-surface'}`}>
                              {pl.name}
                            </span>
                          </span>
                          {pl.desc && (
                            <span className="block text-caption text-on-surface-variant/70 mt-0.5 ml-4 font-mono truncate">{pl.desc}</span>
                          )}
                        </button>
                      ))}
                    </div>
                  </ConsoleStep>

                  {/* ── 素材源 ── */}
                  {materialSources.length > 0 && (
                    <ConsoleStep no="" title="素材源" en="SOURCES" done={selectedSources.length > 0}
                      stat={selectedSources.length > 0 ? `已选 ${selectedSources.length} 个` : undefined}>
                      <p className="text-caption text-on-surface-variant mb-2">选择 Agent 检索素材时使用的来源。不选则使用全部可用源。</p>
                      <div className="flex flex-wrap gap-1.5">
                        {materialSources.map((s) => {
                          const sel = selectedSources.includes(s.id);
                          return (
                            <button key={s.id}
                              onClick={() => setSelectedSources(sel
                                ? selectedSources.filter((x) => x !== s.id)
                                : [...selectedSources, s.id])}
                              className={`px-2.5 py-1 rounded-cw-full text-label-sm border transition-all duration-short3 cursor-pointer ${
                                sel
                                  ? 'bg-track-video/15 border-track-video/60 text-track-video'
                                  : 'bg-surface-container-high border-outline-variant/40 text-on-surface-variant hover:border-outline'
                              }`}
                            >
                              {s.name || s.id}
                            </button>
                          );
                        })}
                      </div>
                    </ConsoleStep>
                  )}

                  {/* ── 03 配音与字幕 ── */}
                  <ConsoleStep no="03" title="配音与字幕" en="DUB & CAPTION" done={stepDone.dub}
                    stat={audio ? `${audio.name} · ${fmtDur(audio.duration)}` : undefined}
                  >
                    <div className="flex items-center gap-3 mb-3">
                      <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
                        <button
                          onClick={() => setMode('voiceover')}
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                            mode === 'voiceover' ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                          }`}
                        >
                          <Mic className="w-3 h-3" /> 配音驱动
                        </button>
                        <button
                          onClick={() => setMode('visual')}
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                            mode === 'visual' ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                          }`}
                        >
                          <ImageIcon className="w-3 h-3" /> 视觉驱动
                        </button>
                      </div>

                      {mode === 'voiceover' && (
                        <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
                          <button
                            onClick={() => setSplitMode('period')}
                            className={`px-2.5 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                              splitMode === 'period' ? 'bg-secondary-container text-secondary' : 'text-on-surface-variant hover:text-on-surface'
                            }`}
                          >
                            按句号切分
                          </button>
                          <button
                            onClick={() => setSplitMode('punctuation')}
                            className={`px-2.5 py-1.5 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                              splitMode === 'punctuation' ? 'bg-secondary-container text-secondary' : 'text-on-surface-variant hover:text-on-surface'
                            }`}
                          >
                            按标点切分
                          </button>
                        </div>
                      )}
                    </div>

                    {mode === 'voiceover' && (
                      <>
                        <input
                          ref={audioInputRef} type="file" accept=".wav,.mp3,.m4a,.mp4" className="hidden"
                          onChange={(e) => { const f = e.target.files?.[0]; if (f) pickAudio(f); e.target.value = ''; }}
                        />
                        {audio ? (
                          <div className="flex items-center gap-3 bg-track-audio/8 border border-track-audio/40 rounded-cw-sm px-3 py-2.5">
                            <AudioLines className="w-4 h-4 text-track-audio shrink-0" />
                            <div className="flex-1 min-w-0">
                              <p className="text-body-sm text-on-surface truncate">{audio.name}</p>
                              <div className="flex items-end gap-0.5 h-3 mt-1">
                                {Array.from({ length: 32 }).map((_, i) => (
                                  <i key={i} className="flex-1 rounded-t-[1px] bg-track-audio/70"
                                    style={{ height: `${20 + 70 * Math.abs(Math.sin(i * 1.7))}%` }} />
                                ))}
                              </div>
                            </div>
                            <span className="font-mono text-caption text-track-audio shrink-0">{fmtDur(audio.duration)}</span>
                            <button onClick={() => setAudio(null)}
                              className="p-1 rounded-cw-xs text-on-surface-variant hover:text-error cursor-pointer shrink-0">
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => audioInputRef.current?.click()}
                            disabled={uploading}
                            className="w-full flex items-center justify-center gap-2 border-2 border-dashed border-outline-variant/40 hover:border-track-audio/60
                              rounded-cw-sm py-3 text-label-sm text-on-surface-variant hover:text-track-audio transition-colors cursor-pointer disabled:opacity-60"
                          >
                            {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                            {uploading ? '上传中…' : '上传配音文件（wav / mp3 / m4a）'}
                          </button>
                        )}
                        {uploadErr && <p className="text-caption text-error mt-1.5">{uploadErr}</p>}

                        {captions.length > 0 && (
                          <div className="mt-3">
                            <p className="flex items-center gap-1.5 text-caption text-on-surface-variant mb-1.5">
                              <Scissors className="w-3 h-3 text-track-caption" />
                              字幕切分预览 · {captions.length} 段
                            </p>
                            <div className="flex flex-wrap gap-1.5">
                              {captions.slice(0, 6).map((c, i) => (
                                <span key={i}
                                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-cw-xs bg-surface-container-high border border-outline-variant/30
                                    text-caption text-on-surface-variant caption-chip"
                                  style={{ animationDelay: `${i * 40}ms` }}
                                >
                                  <b className="font-mono text-track-caption">{String(i + 1).padStart(2, '0')}</b>
                                  <span className="max-w-[180px] truncate">{c}</span>
                                </span>
                              ))}
                              {captions.length > 6 && (
                                <span className="px-2 py-1 rounded-cw-xs text-caption text-on-surface-variant/60 font-mono">
                                  +{captions.length - 6} 段
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                      </>
                    )}

                    {mode === 'visual' && (
                      <p className="text-caption text-on-surface-variant/70 leading-relaxed">
                        视觉驱动模式：文案每一行视为一个场景描述，由素材 Agent 检索匹配画面。
                      </p>
                    )}
                  </ConsoleStep>

                  {/* ── 04 启动 ── */}
                  <div className="border-t border-outline-variant/25 pt-4">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mb-4 font-mono text-caption text-on-surface-variant">
                      <span className="flex items-center gap-1"><FileText className="w-3 h-3" />{captions.length || '—'} 段字幕</span>
                      <span className="flex items-center gap-1"><Clock className="w-3 h-3" />≈ {fmtDur(estDuration)}</span>
                      <span className="flex items-center gap-1"><Mic className="w-3 h-3" />{mode === 'voiceover' ? (audio ? '已上传配音' : '无配音') : '视觉驱动'}</span>
                      <span className="flex items-center gap-1"><Layers className="w-3 h-3" />{plugins.find((p) => p.id === pluginId)?.name ?? pluginId}</span>
                    </div>
                    {launchErr && (
                      <div className="mb-3 bg-error/10 border border-error/30 rounded-cw-sm px-3 py-2 text-label-sm text-error">
                        {launchErr}
                      </div>
                    )}
                    <div className="flex items-center gap-3">
                      <Button size="lg" onClick={launch} disabled={launching} className="flex-1 group">
                        {launching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
                        {launching ? '启动管线中…' : '开始创作'}
                        <ArrowRight className="w-4 h-4 transition-transform duration-short3 group-hover:translate-x-1" />
                      </Button>
                      <Button size="lg" variant="outline" onClick={openBlank}>
                        <Plus className="w-4 h-4" />
                        空白编辑器
                      </Button>
                      <Button size="lg" variant="ghost" onClick={() => setTemplateGalleryOpen(true)}
                        className="text-on-surface-variant hover:text-primary">
                        <LayoutTemplate className="w-4 h-4" />
                        从模板开始
                      </Button>
                    </div>
                    {/* P8: dry-run 预览模式开关 */}
                    <label className="mt-3 flex items-center gap-2 text-label-sm text-on-surface-variant cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={dryRun}
                        onChange={(e) => setDryRun(e.target.checked)}
                        className="accent-primary w-3.5 h-3.5"
                      />
                      仅预览（dry-run）— 只生成粗剪时间线，跳过动画/音频/质检，快速出规划
                    </label>
                  </div>
                </div>
              </div>
            </div>

            {/* ── workflow stepper + status ── */}
            <div className="col-span-12 lg:col-span-5 lg:pl-4">
              <p className="font-mono text-label-sm tracking-[0.25em] text-on-surface-variant uppercase mb-4 mt-1">
                Human-in-the-loop 工作流
              </p>
              <div className="relative space-y-0">
                <span className="absolute left-[19px] top-3 bottom-3 w-px bg-gradient-to-b from-track-video via-track-text to-track-audio opacity-40" />
                {WORKFLOW.map((step, i) => (
                  <div key={step.title} className="relative flex gap-4 pb-6 last:pb-0 group">
                    <span
                      className="relative z-10 w-10 h-10 rounded-cw-md flex items-center justify-center shrink-0 border transition-transform duration-short3 group-hover:scale-110"
                      style={{ background: `${step.color}1A`, borderColor: `${step.color}66`, color: step.color }}
                    >
                      <step.icon className="w-4.5 h-4.5" />
                    </span>
                    <div className="pt-0.5">
                      <p className="flex items-center gap-2">
                        <span className="font-mono text-caption text-on-surface-variant">0{i + 1}</span>
                        <span className="text-title-sm font-semibold text-on-surface">{step.title}</span>
                        {step.highlight && <Badge variant="warning">核心</Badge>}
                      </p>
                      <p className="text-body-sm text-on-surface-variant mt-0.5 leading-relaxed">{step.desc}</p>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-6 bg-surface-container border border-outline-variant/30 rounded-cw-md p-4">
                <div className="flex items-center justify-between">
                  <span className="text-label font-medium text-on-surface-variant">编排引擎</span>
                  <span className="flex items-center gap-1.5 text-label-sm">
                    <i className={`w-2 h-2 rounded-full ${
                      backend === 'online' ? 'bg-track-audio animate-pulse'
                        : backend === 'offline' ? 'bg-error'
                        : 'bg-track-text animate-pulse'
                    }`} />
                    <span className={
                      backend === 'online' ? 'text-track-audio'
                        : backend === 'offline' ? 'text-error'
                        : 'text-track-text'
                    }>
                      {backend === 'online' ? `已连接 ${(() => { try { return new URL(getApiClient().defaults.baseURL || 'http://localhost:8000').host; } catch { return 'localhost:8000'; } })()}`
                        : backend === 'offline' ? '离线 · 演示模式'
                        : '检测中…'}
                    </span>
                  </span>
                </div>
                <p className="text-caption text-on-surface-variant/70 mt-1.5 leading-relaxed">
                  {backend === 'offline'
                    ? '未检测到后端。编辑器仍可独立使用，Agent 功能将以演示数据运行。'
                    : 'Persona 引擎、六 Agent 管线与渲染队列已就绪。'}
                </p>
              </div>
            </div>
          </section>
          )}

          {/* 底部快捷键提示 */}
          <footer className="border-t border-outline-variant/20 py-3 mt-10">
            <div className="flex items-center gap-6 text-caption text-on-surface-variant/60 font-mono">
              <span>空格 = 播放/暂停</span>
              <span>S = 分割</span>
              <span>Del = 删除</span>
              <span>Ctrl+滚轮 = 缩放</span>
              <span>M = 标记</span>
              <span className="ml-auto">ClipWright v0.1.0 · Phase 5</span>
            </div>
          </footer>
        </main>
      </div>

      <style>{`
        @keyframes captionChipIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .caption-chip { animation: captionChipIn 0.25s var(--ease-emphasized-decelerate) both; }
      `}</style>

      {/* A3: 模板画廊 */}
      <TemplateGallery
        open={templateGalleryOpen}
        onClose={() => setTemplateGalleryOpen(false)}
        onApplyProject={(projectId) => navigate({ to: '/editor/$projectId', params: { projectId } })}
      />
    </div>
  );
}

/* ── 设置 tab：轻量系统设置（复用 settingsStore）──────────────── */
function HomeSettings({ onAdvanced }: { onAdvanced: () => void }) {
  const s = useSettingsStore();
  const navigate = useNavigate();

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      {/* 外观 */}
      <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-5 space-y-4">
        <h3 className="flex items-center gap-2 text-title-sm font-medium text-on-surface">
          <Palette className="w-4 h-4 text-primary" /> 外观
        </h3>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">主题</span>
          <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
            {(['dark', 'light'] as const).map((t) => (
              <button key={t}
                onClick={() => s.setTheme(t)}
                className={`px-3 py-1 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                  s.theme === t ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                }`}>
                {t === 'dark' ? '深色' : '浅色'}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">语言</span>
          <div className="flex bg-surface rounded-cw-sm border border-outline-variant/40 p-0.5">
            {(['zh', 'en'] as const).map((l) => (
              <button key={l}
                onClick={() => s.setLanguage(l)}
                className={`px-3 py-1 rounded-cw-xs text-label-sm transition-colors cursor-pointer ${
                  s.language === l ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-variant hover:text-on-surface'
                }`}>
                {l === 'zh' ? '中文' : 'English'}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">自动保存</span>
          <input type="checkbox" checked={s.autoSave} onChange={(e) => s.setAutoSave(e.target.checked)}
            className="accent-primary w-4 h-4 cursor-pointer" />
        </div>
      </div>

      {/* 时间轴默认 */}
      <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-5 space-y-4">
        <h3 className="flex items-center gap-2 text-title-sm font-medium text-on-surface">
          <Ruler className="w-4 h-4 text-primary" /> 时间轴默认
        </h3>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">帧率 FPS</span>
          <input type="number" value={s.defaultFps} min={1} max={120}
            onChange={(e) => s.setDefaultFps(Number(e.target.value) || 30)}
            className="w-20 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">默认分辨率</span>
          <div className="flex items-center gap-1.5">
            <input type="number" value={s.defaultResolution.width}
              onChange={(e) => s.setDefaultResolution({ ...s.defaultResolution, width: Number(e.target.value) || 1920 })}
              className="w-20 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
            <span className="text-on-surface-variant">×</span>
            <input type="number" value={s.defaultResolution.height}
              onChange={(e) => s.setDefaultResolution({ ...s.defaultResolution, height: Number(e.target.value) || 1080 })}
              className="w-20 bg-surface rounded-cw-xs px-2 py-1 text-label-sm font-mono text-on-surface outline-none border border-outline-variant/30 focus:border-primary" />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">吸附（对齐）</span>
          <input type="checkbox" checked={s.snapEnabled} onChange={(e) => s.setSnapEnabled(e.target.checked)}
            className="accent-primary w-4 h-4 cursor-pointer" />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-on-surface-variant">时间轴标尺显示帧标记</span>
          <input type="checkbox" checked={s.showFramesInRuler} onChange={(e) => s.setShowFramesInRuler(e.target.checked)}
            className="accent-primary w-4 h-4 cursor-pointer" />
        </div>
      </div>

      {/* 高级设置入口 */}
      <div className="lg:col-span-2 bg-surface-container border border-outline-variant/30 rounded-cw-md p-5">
        <h3 className="flex items-center gap-2 text-title-sm font-medium text-on-surface mb-3">
          <Server className="w-4 h-4 text-primary" /> 高级
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="outline" onClick={onAdvanced}>
            <ChevronRight className="w-3.5 h-3.5" /> 完整设置页（API / 快捷键 / 模型 / 工具…）
          </Button>
          <Button size="sm" variant="ghost" onClick={() => navigate({ to: '/pipeline-admin' })}>
            <Gauge className="w-3.5 h-3.5" /> 管线监控
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ── 图一：左侧栏（用户卡片 + 导航）───────────────────── */
function HomeSidebar({ activeTab, onTab, credit, user, onTopup, estimate }: {
  activeTab: HomeTab;
  onTab: (t: HomeTab) => void;
  credit: number | null;
  user: { display_name?: string; user_id?: string; email?: string } | null;
  onTopup: () => void;
  estimate: number | null;
}) {
  const navigate = useNavigate();
  // 拆分成 primitive selector，避免返回新对象引用导致无限重渲染
  const authUser = useAuthStore((s) => s.user);
  const authToken = useAuthStore((s) => s.accessToken);
  const authLogout = useAuthStore((s) => s.logout);

  const NAV: { id: HomeTab; label: string; icon: typeof Home }[] = [
    { id: 'home', label: '首页', icon: Home },
    { id: 'types', label: '类型', icon: Shapes },
    { id: 'personas', label: '人格', icon: User },
    { id: 'plugins', label: '插件', icon: Puzzle },
    { id: 'settings', label: '设置', icon: Settings },
  ];

  return (
    <aside className="w-[236px] shrink-0 border-r border-outline-variant/25 bg-surface-container-low/60 flex flex-col sticky top-0 h-screen">
      {/* brand */}
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
        <div className="w-9 h-9 rounded-cw-sm bg-primary-container flex items-center justify-center shadow-lg shadow-primary/20">
          <Film className="w-5 h-5 text-on-primary-container" />
        </div>
        <div className="leading-tight">
          <p className="text-title-sm font-bold text-on-surface tracking-wide">帧艺</p>
          <p className="font-mono text-caption text-on-surface-variant tracking-[0.2em]">CLIPWRIGHT</p>
        </div>
      </div>

      {/* 用户信息卡片（图一：昵称 / ID / CREADIT） */}
      <div className="mx-4 mb-5 bg-surface-container border border-outline-variant/30 rounded-cw-md p-3.5">
        <div className="flex items-center gap-2.5">
          <span className="w-10 h-10 rounded-cw-full bg-primary-container flex items-center justify-center shrink-0 ring-1 ring-on-primary-container/20">
            <User className="w-5 h-5 text-on-primary-container" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-body-sm font-semibold text-on-surface truncate">
              {authUser?.display_name || user?.display_name || '未登录'}
            </p>
            <p className="font-mono text-caption text-on-surface-variant/70 truncate">
              {authUser?.user_id ? `ID ${authUser.user_id.slice(0, 8)}` : 'ID ———'}
            </p>
          </div>
        </div>
        <div className="mt-2.5 pt-2.5 border-t border-outline-variant/20 flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
            <Coins className="w-3.5 h-3.5 text-track-text" />
            CREADIT
          </span>
          <span className="font-mono text-body-sm font-bold text-track-text">
            {credit ?? '—'}
          </span>
        </div>
        {authToken && (
          <button
            onClick={onTopup}
            className="mt-2 w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-cw-xs
              bg-track-text/10 border border-track-text/30 text-track-text text-label-sm
              hover:bg-track-text/20 transition-colors cursor-pointer"
            title="测试充值 +100"
          >
            <Zap className="w-3 h-3" /> 充值 +100（测试）
          </button>
        )}
        {estimate !== null && credit !== null && (
          <p className="mt-2 text-caption font-mono text-on-surface-variant/70">
            本次预估 <b className="text-on-surface">{estimate}</b> CREADIT
          </p>
        )}
      </div>

      {/* 导航（图一：首页/类型/人格/插件/设置）——点击直接切换右侧内容区 */}
      <nav className="flex-1 px-3 space-y-1">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onTab(id)}
            className={`w-full flex items-center gap-2.5 px-3.5 py-2.5 rounded-cw-sm text-label-sm font-medium transition-all duration-short3 cursor-pointer ${
              activeTab === id
                ? 'bg-primary-container text-on-primary-container shadow-md shadow-primary/20'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </nav>

      {/* 底部：账号操作 + 其他入口 */}
      <div className="px-3 pb-5 pt-3 space-y-1 border-t border-outline-variant/20">
        {authToken ? (
          <button
            onClick={async () => { await authLogout(); navigate({ to: '/' }); }}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 rounded-cw-sm text-label-sm text-on-surface-variant
              hover:text-error hover:bg-error/5 transition-colors cursor-pointer"
          >
            <LogOut className="w-4 h-4" /> 退出登录
          </button>
        ) : (
          <button
            onClick={() => navigate({ to: '/login' })}
            className="w-full flex items-center gap-2.5 px-3.5 py-2 rounded-cw-sm text-label-sm text-on-surface-variant
              hover:text-primary hover:bg-primary/5 transition-colors cursor-pointer"
          >
            <LogIn className="w-4 h-4" /> 登录 / 注册
          </button>
        )}
        <button
          onClick={() => navigate({ to: '/projects' })}
          className="w-full flex items-center gap-2.5 px-3.5 py-2 rounded-cw-sm text-label-sm text-on-surface-variant
            hover:text-on-surface hover:bg-surface-container transition-colors cursor-pointer"
        >
          <FolderOpen className="w-4 h-4" /> 我的项目
        </button>
        <button
          onClick={() => navigate({ to: '/voice' })}
          className="w-full flex items-center gap-2.5 px-3.5 py-2 rounded-cw-sm text-label-sm text-on-surface-variant
            hover:text-on-surface hover:bg-surface-container transition-colors cursor-pointer"
        >
          <Mic className="w-4 h-4" /> 音色库
        </button>
      </div>
    </aside>
  );
}

/* ── console step header ───────────────────────────────── */
function ConsoleStep({ no, title, en, done, stat, children }: {
  no: string; title: string; en: string; done: boolean;
  stat?: string; children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-2.5 mb-3">
        <span className={`w-6 h-6 rounded-cw-xs flex items-center justify-center font-mono text-caption border transition-all duration-medium2 ${
          done
            ? 'bg-track-audio/15 border-track-audio/60 text-track-audio'
            : 'bg-surface-container-high border-outline-variant/40 text-on-surface-variant'
        }`}>
          {done ? <Check className="w-3.5 h-3.5" /> : no}
        </span>
        <span className="text-title-sm font-semibold text-on-surface">{title}</span>
        <span className="font-mono text-caption tracking-[0.2em] text-on-surface-variant/50 uppercase">{en}</span>
        {stat && (
          <span className="ml-auto font-mono text-caption text-primary/80 bg-primary/8 border border-primary/20 rounded-cw-full px-2 py-0.5">
            {stat}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

/* ── decorative components ─────────────────────────────── */
function Backdrop() {
  return (
    <div className="absolute inset-0 pointer-events-none overflow-hidden" aria-hidden>
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage:
            'linear-gradient(to right, #8D8D99 1px, transparent 1px), linear-gradient(to bottom, #8D8D99 1px, transparent 1px)',
          backgroundSize: '56px 56px',
        }}
      />
      <div
        className="absolute -top-40 -left-40 w-[640px] h-[640px] rounded-full opacity-[0.14]"
        style={{ background: 'radial-gradient(circle, #4F6BED 0%, transparent 65%)' }}
      />
      <div
        className="absolute -bottom-48 -right-32 w-[560px] h-[560px] rounded-full opacity-[0.08]"
        style={{ background: 'radial-gradient(circle, #D1708E 0%, transparent 65%)' }}
      />
    </div>
  );
}

function RulerStrip() {
  const ticks = Array.from({ length: 80 });
  return (
    <div className="relative h-7 border-b border-outline-variant/25 bg-ruler-bg overflow-hidden select-none" aria-hidden>
      <div className="absolute inset-0 flex items-end">
        {ticks.map((_, i) => (
          <span
            key={i}
            className={`w-px shrink-0 ${i % 5 === 0 ? 'h-3 bg-ruler-tick' : 'h-1.5 bg-ruler-tick/50'}`}
            style={{ marginRight: i % 5 === 0 ? '34px' : '6px' }}
          />
        ))}
      </div>
      <span className="absolute top-0 bottom-0 w-px bg-playhead shadow-[0_0_8px_rgba(255,68,68,0.8)] ruler-sweep" />
      <style>{`
        @keyframes rulerSweep { 0% { left: -2%; } 100% { left: 102%; } }
        .ruler-sweep { animation: rulerSweep 9s linear infinite; }
      `}</style>
    </div>
  );
}

// RulerStrip 与旧 TopBar 已由图一左侧栏（HomeSidebar）替代，不再使用。


