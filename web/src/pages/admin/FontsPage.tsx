import { useEffect, useState } from 'react';
import { ConsoleShell, ConsoleHeading } from './ConsoleShell';
import { fontApi } from '@/services/api';
import { Badge } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Check } from 'lucide-react';

interface FontItem {
  family: string;
  style: string;
  source: 'system' | 'backend' | 'web';
  supportsCjk: boolean;
}

const SAMPLES = ['帧艺 ClipWright 让创作更自由', 'The quick brown fox jumps over the lazy dog', '0123456789 → 视频剪辑'];

/**
 * FontsPage — a live type tester. Each font renders the sample string so you
 * can judge CJK/Latin coverage before assigning it as the editor default.
 */
export function FontsPage() {
  const [fonts, setFonts] = useState<FontItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sample, setSample] = useState(SAMPLES[0]);
  const [size, setSize] = useState(28);
  const [defaultFont, setDefaultFont] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fontApi.list();
        if (alive) setFonts(normalize(res.fonts));
      } catch {
        if (alive) setFonts(DEMO_FONTS);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <ConsoleShell>
      <ConsoleHeading kicker="Appearance / Fonts" title="字体配置"
        desc="预览引擎渲染视频内文字时使用的字体。选择默认字体前，可实时预览样张。" />

      {/* type tester controls */}
      <div className="bg-surface-container border border-outline-variant/30 rounded-cw-md p-4 mb-6 max-w-[820px]">
        <div className="flex items-center gap-3 flex-wrap">
          <input value={sample} onChange={(e) => setSample(e.target.value)}
            className="flex-1 min-w-[240px] bg-surface rounded-cw-sm px-3 py-2 text-body-sm text-on-surface
              outline-none border border-outline-variant/30 focus:border-primary" />
          <div className="flex items-center gap-2">
            <span className="text-label text-on-surface-variant">字号</span>
            <input type="range" min={14} max={56} value={size} onChange={(e) => setSize(Number(e.target.value))}
              className="accent-primary cursor-pointer w-32" />
            <span className="font-mono text-caption text-primary w-10">{size}px</span>
          </div>
        </div>
      </div>

      {/* specimen list */}
      <div className="space-y-3 max-w-[820px]">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-24 bg-surface-container rounded-cw-md animate-pulse" />)
        ) : (
          fonts.map((f) => {
            const isDefault = defaultFont === f.family;
            return (
              <button key={f.family + f.style}
                onClick={() => setDefaultFont(f.family)}
                className={cn('w-full text-left bg-surface-container border rounded-cw-md px-5 py-4 transition-all duration-short3 group cursor-pointer',
                  isDefault ? 'border-primary/60 shadow-lg shadow-primary/10' : 'border-outline-variant/30 hover:border-outline/60 hover:-translate-y-0.5')}>
                <div className="flex items-center gap-2.5 mb-2.5">
                  <span className="font-mono text-body-sm font-semibold text-on-surface">{f.family}</span>
                  <span className="font-mono text-caption text-on-surface-variant">{f.style}</span>
                  <Badge variant={f.source === 'system' ? 'default' : f.source === 'backend' ? 'info' : 'warning'}>
                    {f.source === 'system' ? '系统' : f.source === 'backend' ? '后端' : 'Web'}
                  </Badge>
                  {f.supportsCjk && <Badge variant="success">CJK</Badge>}
                  {isDefault && (
                    <span className="ml-auto flex items-center gap-1 text-label-sm text-primary">
                      <Check className="w-3.5 h-3.5" /> 默认
                    </span>
                  )}
                </div>
                {/* live specimen */}
                <p className="text-on-surface leading-snug truncate transition-all duration-short3"
                  style={{ fontFamily: `'${f.family}', 'Noto Sans SC', sans-serif`, fontSize: `${size}px`, fontWeight: f.style === 'Bold' ? 700 : 400 }}>
                  {sample || '输入预览文字…'}
                </p>
              </button>
            );
          })
        )}
      </div>
    </ConsoleShell>
  );
}

function normalize(data: unknown): FontItem[] {
  if (Array.isArray(data)) {
    return data.map((d) => {
      const o = (typeof d === 'string' ? { family: d } : d) as Record<string, unknown>;
      return {
        family: String(o.family ?? o.name ?? 'Font'),
        style: String(o.style ?? 'Regular'),
        source: (o.source as FontItem['source']) ?? 'system',
        supportsCjk: Boolean(o.supports_cjk ?? o.supportsCjk ?? true),
      };
    });
  }
  return [];
}

const DEMO_FONTS: FontItem[] = [
  { family: 'Noto Sans SC', style: 'Regular', source: 'web', supportsCjk: true },
  { family: 'Noto Sans SC', style: 'Bold', source: 'web', supportsCjk: true },
  { family: 'JetBrains Mono', style: 'Regular', source: 'web', supportsCjk: false },
  { family: 'PingFang SC', style: 'Regular', source: 'system', supportsCjk: true },
  { family: 'Microsoft YaHei', style: 'Regular', source: 'system', supportsCjk: true },
  { family: 'Source Han Serif', style: 'Bold', source: 'backend', supportsCjk: true },
];
