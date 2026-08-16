import { useMemo } from 'react';
import type { ReactNode } from 'react';

/**
 * Markdown — a lightweight, dependency-free renderer for the subset of
 * Markdown the Agents emit (headings, bold/italic, lists, code, quotes).
 * Renders to React elements (no dangerouslySetInnerHTML), so it is safe.
 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return <div className={className}>{blocks}</div>;
}

function parseBlocks(text: string): ReactNode[] {
  const lines = text.split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    if (line.trim().startsWith('```')) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      out.push(
        <pre key={key++} className="bg-surface border border-outline-variant/30 rounded-cw-xs px-3 py-2 my-2 font-mono text-caption text-track-audio overflow-x-auto leading-relaxed">
          {buf.join('\n')}
        </pre>,
      );
      continue;
    }

    // heading
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const content = renderInline(h[2]);
      const cls =
        level === 1 ? 'text-title-sm font-bold text-on-surface mt-3 mb-1.5 pb-1 border-b border-outline-variant/30'
        : level === 2 ? 'text-body-sm font-bold text-primary mt-3 mb-1'
        : 'text-body-sm font-semibold text-on-surface mt-2 mb-1';
      out.push(<div key={key++} className={cls}>{content}</div>);
      i++;
      continue;
    }

    // blockquote
    if (line.trim().startsWith('>')) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      out.push(
        <div key={key++} className="border-l-2 border-primary/50 pl-3 my-2 text-body-sm text-on-surface-variant italic">
          {renderInline(buf.join(' '))}
        </div>,
      );
      continue;
    }

    // unordered list
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, ''));
        i++;
      }
      out.push(
        <ul key={key++} className="space-y-1 my-2">
          {items.map((it, j) => (
            <li key={j} className="flex gap-2 text-body-sm text-on-surface leading-relaxed">
              <span className="text-primary shrink-0 mt-[7px] w-1 h-1 rounded-full bg-primary" />
              <span>{renderInline(it)}</span>
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    // ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
        i++;
      }
      out.push(
        <ol key={key++} className="space-y-1 my-2">
          {items.map((it, j) => (
            <li key={j} className="flex gap-2 text-body-sm text-on-surface leading-relaxed">
              <span className="font-mono text-caption text-primary shrink-0 mt-0.5">{j + 1}.</span>
              <span>{renderInline(it)}</span>
            </li>
          ))}
        </ol>,
      );
      continue;
    }

    // blank line
    if (line.trim() === '') {
      i++;
      continue;
    }

    // paragraph
    out.push(<p key={key++} className="text-body-sm text-on-surface leading-relaxed my-1">{renderInline(line)}</p>);
    i++;
  }

  return out;
}

/** Render inline markdown (bold, italic, inline code) to React nodes. */
function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Tokenize: `code`, **bold**, *italic*
  const regex = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('`')) {
      nodes.push(<code key={k++} className="font-mono text-caption bg-surface-container-high text-track-text px-1 py-px rounded-[3px]">{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={k++} className="font-semibold text-on-surface">{tok.slice(2, -2)}</strong>);
    } else {
      nodes.push(<em key={k++} className="italic">{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
