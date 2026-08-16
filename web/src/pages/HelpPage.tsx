import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { StandardLayout } from '@/layouts/StandardLayout';
import {
  ArrowLeft, Keyboard, MousePointer2, Film, Bot, Layers,
  BookOpen, Zap, Search,
} from 'lucide-react';

/* ── shortcut data ─────────────────────────────────────── */
const SHORTCUT_GROUPS: { title: string; icon: typeof Keyboard; items: { keys: string[]; action: string }[] }[] = [
  {
    title: '传输控制', icon: Film,
    items: [
      { keys: ['空格'], action: '播放 / 暂停' },
      { keys: ['←'], action: '上一帧' },
      { keys: ['→'], action: '下一帧' },
      { keys: ['Home'], action: '跳到开头' },
      { keys: ['End'], action: '跳到结尾' },
    ],
  },
  {
    title: '时间轴编辑', icon: Layers,
    items: [
      { keys: ['S'], action: '在播放头处分割片段' },
      { keys: ['Del'], action: '删除选中片段' },
      { keys: ['M'], action: '添加标记点' },
      { keys: ['Shift', '点击'], action: '多选片段' },
      { keys: ['V'], action: '选择工具' },
      { keys: ['B'], action: '剃刀工具' },
    ],
  },
  {
    title: '视图导航', icon: MousePointer2,
    items: [
      { keys: ['Ctrl', '滚轮'], action: '以光标为中心缩放' },
      { keys: ['+'], action: '放大时间轴' },
      { keys: ['-'], action: '缩小时间轴' },
      { keys: ['Shift', '滚轮'], action: '横向滚动' },
      { keys: ['中键拖动'], action: '平移视图' },
    ],
  },
  {
    title: '通用', icon: Zap,
    items: [
      { keys: ['Ctrl', 'Z'], action: '撤销' },
      { keys: ['Ctrl', 'Shift', 'Z'], action: '重做' },
      { keys: ['Ctrl', '/'], action: '快捷键速查表' },
    ],
  },
];

const CONCEPTS = [
  { term: 'Persona 人格', def: '可训练、可迁移的创作者数字分身。从语言措辞到剪辑节奏全部参数化，Agent 按它出初稿。用得越多，越像你。', color: '#A855F7' },
  { term: '类型插件', def: '封装不同视频类型的剪辑逻辑差异——知识区长片、鬼畜快剪、数码评测、Vlog 日常，各有不同的镜头时长与转场策略。', color: '#4F8CFF' },
  { term: '时间线 JSON', def: '前后端统一的数据契约。Agent 输出、用户编辑、渲染导出都在同一个数据模型上操作，这是互操作性的基础。', color: '#34D399' },
  { term: '人在回路', def: 'Agent 负责结构化与体力活，人负责审美判断与微调。不是自动驾驶，是副驾驶。', color: '#FBBF24' },
];

/**
 * HelpPage — the editor's field manual: searchable shortcut reference,
 * workflow walkthrough, and core concepts. Doubles as the Ctrl+/ cheat sheet.
 */
export function HelpPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  const filteredGroups = SHORTCUT_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter(
      (it) =>
        it.action.toLowerCase().includes(query.toLowerCase()) ||
        it.keys.some((k) => k.toLowerCase().includes(query.toLowerCase())),
    ),
  })).filter((g) => g.items.length > 0);

  return (
    <StandardLayout title="帮助与教程">
      <button onClick={() => navigate({ to: '/' })}
        className="flex items-center gap-1.5 text-label-sm text-on-surface-variant hover:text-primary transition-colors mb-6 cursor-pointer">
        <ArrowLeft className="w-3.5 h-3.5" /> 返回工作台
      </button>

      <div className="max-w-[1000px] grid grid-cols-12 gap-8">
        {/* ── shortcuts ── */}
        <section className="col-span-12 lg:col-span-7">
          <div className="flex items-center justify-between mb-4">
            <h2 className="flex items-center gap-2 text-title font-semibold text-on-surface">
              <Keyboard className="w-5 h-5 text-primary" /> 快捷键
            </h2>
            <div className="flex items-center gap-2 bg-surface-container rounded-cw-sm px-3 py-1.5 border border-outline-variant/30 focus-within:border-primary transition-colors">
              <Search className="w-3.5 h-3.5 text-on-surface-variant shrink-0" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索快捷键…"
                className="bg-transparent outline-none text-body-sm text-on-surface placeholder:text-on-surface-variant/50 w-36"
              />
            </div>
          </div>

          <div className="space-y-5">
            {filteredGroups.map((group) => (
              <div key={group.title}>
                <h3 className="flex items-center gap-2 text-label font-medium text-on-surface-variant uppercase tracking-wide mb-2.5">
                  <group.icon className="w-3.5 h-3.5" /> {group.title}
                </h3>
                <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md divide-y divide-outline-variant/20 overflow-hidden">
                  {group.items.map((item) => (
                    <div key={item.action}
                      className="flex items-center justify-between px-4 py-2.5 hover:bg-primary/5 transition-colors duration-short3">
                      <span className="text-body-sm text-on-surface">{item.action}</span>
                      <span className="flex items-center gap-1">
                        {item.keys.map((k, i) => (
                          <span key={i} className="flex items-center gap-1">
                            {i > 0 && <span className="text-caption text-on-surface-variant/50">+</span>}
                            <Kbd>{k}</Kbd>
                          </span>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {filteredGroups.length === 0 && (
              <p className="text-body-sm text-on-surface-variant py-8 text-center">没有匹配「{query}」的快捷键</p>
            )}
          </div>
        </section>

        {/* ── concepts + workflow ── */}
        <section className="col-span-12 lg:col-span-5 space-y-8">
          <div>
            <h2 className="flex items-center gap-2 text-title font-semibold text-on-surface mb-4">
              <BookOpen className="w-5 h-5 text-primary" /> 核心概念
            </h2>
            <div className="space-y-3">
              {CONCEPTS.map((c) => (
                <div key={c.term}
                  className="relative bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 pl-5 overflow-hidden
                    hover:border-outline/60 transition-colors duration-short3 group">
                  <span className="absolute left-0 top-0 bottom-0 w-1 transition-all duration-short3 group-hover:w-1.5"
                    style={{ background: c.color }} />
                  <p className="text-body-sm font-semibold text-on-surface">{c.term}</p>
                  <p className="text-label-sm text-on-surface-variant leading-relaxed mt-1">{c.def}</p>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="flex items-center gap-2 text-title font-semibold text-on-surface mb-4">
              <Bot className="w-5 h-5 text-primary" /> 创作流程
            </h2>
            <ol className="relative space-y-4 before:absolute before:left-[13px] before:top-2 before:bottom-2 before:w-px before:bg-outline-variant/40">
              {[
                { t: '输入选题', d: '在工作台输入选题，选择人格与视频类型。' },
                { t: '需求规划', d: '需求 Agent 生成创意简报与制作规划书，确认后触发管线。' },
                { t: 'Agent 出初稿', d: '六 Agent 管线产出多轨时间线，实时可见进度。' },
                { t: '时间轴精修', d: '逐帧审视，拖拽、分割、调属性，不满意可让 Agent 局部重做。' },
                { t: '渲染导出', d: '选择平台预设，加入渲染队列，下载成片。' },
              ].map((step, i) => (
                <li key={step.t} className="relative flex gap-3.5">
                  <span className="relative z-10 w-7 h-7 rounded-cw-full bg-primary-container text-on-primary-container flex items-center justify-center text-label font-mono font-semibold shrink-0">
                    {i + 1}
                  </span>
                  <div className="pt-0.5">
                    <p className="text-body-sm font-medium text-on-surface">{step.t}</p>
                    <p className="text-label-sm text-on-surface-variant leading-relaxed mt-0.5">{step.d}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </div>
    </StandardLayout>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[26px] h-[24px] px-1.5
      bg-surface-container-high border border-outline-variant/50 border-b-2 rounded-cw-xs
      font-mono text-label-sm text-on-surface shadow-sm">
      {children}
    </kbd>
  );
}
