/**
 * PluginLayoutRenderer — JSON-driven UI rendering engine.
 *
 * Renders a UILayout tree into React components. Plugin developers define
 * declarative JSON layouts; this engine handles state management, input
 * binding, API calls, and conditional rendering.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { getApiClient } from '@/services/api';
import { Button } from '@/components/ui';
import { Loader2, AlertCircle, CheckCircle2, Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { UILayout, UIWidget, UIRenderState } from './types';

export function PluginLayoutRenderer({ layout }: { layout: UILayout }) {
  const [state, setState] = useState<UIRenderState>({});
  const updatingRef = useRef(false);
  // 动作序号：丢弃过期响应，避免快速连点导致旧响应覆盖新状态
  const actionSeqRef = useRef(0);

  const setUIState = useCallback((key: string, value: unknown) => {
    if (updatingRef.current) return;
    setState((s) => ({ ...s, [key]: value }));
  }, []);

  const getValue = useCallback(
    (key: string | undefined) => (key ? state[key] : undefined),
    [state],
  );

  const handleAction = useCallback(
    async (widget: { type: 'button'; label: string; action: import('./types').UIAction; disabledWhen?: string }) => {
      const { action } = widget;
      // Interpolate "${key}" references in body
      const resolveBody = (obj: Record<string, unknown>): Record<string, unknown> => {
        const result: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(obj)) {
          if (typeof v === 'string' && v.startsWith('${') && v.endsWith('}')) {
            const fieldKey = v.slice(2, -1);
            result[k] = state[fieldKey] ?? '';
          } else if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
            result[k] = resolveBody(v as Record<string, unknown>);
          } else {
            result[k] = v;
          }
        }
        return result;
      };

      const body = action.body ? resolveBody(action.body) : undefined;
      const seq = ++actionSeqRef.current;

      // Set loading state
      if (action.loadingKey) {
        updatingRef.current = true;
        setState((s) => ({ ...s, [action.loadingKey!]: true, [action.errorKey || '']: null }));
        updatingRef.current = false;
      }

      try {
        const config =
          action.method === 'GET'
            ? { params: body }
            : { headers: { 'Content-Type': 'application/json' } };
        const res =
          action.method === 'GET'
            ? await getApiClient().get(action.endpoint, config)
            : await getApiClient().post(action.endpoint, body, config);

        const data = res.data;
        const updates: Record<string, unknown> = {};

        if (action.successKey) updates[action.successKey] = true;
        if (action.loadingKey) updates[action.loadingKey] = false;

        // Map response fields to state
        if (action.resultMap) {
          for (const [stateKey, dataPath] of Object.entries(action.resultMap)) {
            const val = dataPath.split('.').reduce((obj: unknown, k: string) => {
              if (obj && typeof obj === 'object') return (obj as Record<string, unknown>)[k];
              return undefined;
            }, data);
            if (val !== undefined) updates[stateKey] = val;
          }
        } else {
          // Default: merge data into state
          if (data && typeof data === 'object') Object.assign(updates, data);
        }

        if (action.errorKey) updates[action.errorKey] = null;
        if (seq !== actionSeqRef.current) return; // 过期响应，丢弃
        updatingRef.current = true;
        setState((s) => ({ ...s, ...updates }));
        updatingRef.current = false;
      } catch (e: unknown) {
        if (seq !== actionSeqRef.current) return;
        const errMsg = e instanceof Error ? e.message : '请求失败';
        const updates: Record<string, unknown> = {};
        if (action.loadingKey) updates[action.loadingKey] = false;
        if (action.errorKey) updates[action.errorKey] = errMsg;
        updatingRef.current = true;
        setState((s) => ({ ...s, ...updates }));
        updatingRef.current = false;
      }
    },
    [state],
  );

  const renderWidget = useMemo(
    () =>
      (widget: UIWidget, idx: number): React.ReactNode => {
        const vis = widget.visibleWhen ? getValue(widget.visibleWhen) : true;
        if (!vis) return null;

        switch (widget.type) {
          case 'textarea': {
            const val = (getValue(widget.key) as string) ?? widget.defaultValue ?? '';
            return (
              <div key={widget.key}>
                {widget.label && (
                  <label className="block text-label text-on-surface-variant mb-1">{widget.label}</label>
                )}
                <textarea
                  value={val}
                  onChange={(e) => setUIState(widget.key, e.target.value)}
                  placeholder={widget.placeholder}
                  rows={widget.rows ?? 3}
                  className="w-full bg-surface-container rounded-cw-sm px-3 py-2 text-body-sm text-on-surface
                    outline-none border border-outline-variant/30 focus:border-primary resize-none
                    placeholder:text-on-surface-variant/40"
                />
              </div>
            );
          }

          case 'button': {
            const disabled = widget.disabledWhen ? !!getValue(widget.disabledWhen) : false;
            return (
              <Button
                key={widget.label}
                size="sm"
                onClick={() => handleAction(widget)}
                disabled={disabled}
                className="w-full"
              >
                {widget.label}
              </Button>
            );
          }

          case 'image': {
            const src = (getValue(widget.sourceField) as string) ?? '';
            if (!src) return null;
            return (
              <div key={widget.sourceField} className="rounded-cw-sm overflow-hidden border border-outline-variant/20">
                <img src={src} alt={widget.alt ?? ''} className="w-full" />
              </div>
            );
          }

          case 'spinner': {
            return (
              <div key="spinner" className="flex items-center justify-center gap-2 py-2">
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
                {widget.label && <span className="text-caption text-on-surface-variant">{widget.label}</span>}
              </div>
            );
          }

          case 'alert': {
            const msg = (getValue(widget.textField) as string) ?? '';
            if (!msg) return null;
            const colors = {
              error: 'border-error/20 bg-error/5 text-error',
              info: 'border-primary/20 bg-primary/5 text-primary',
              success: 'border-track-video/20 bg-track-video/5 text-track-video',
            };
            const icons = { error: AlertCircle, info: Info, success: CheckCircle2 };
            const Icon = icons[widget.severity];
            return (
              <div
                key={widget.textField}
                className={cn('flex items-start gap-2 px-3 py-2 rounded-cw-sm border text-caption', colors[widget.severity])}
              >
                <Icon className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{msg}</span>
              </div>
            );
          }

          case 'text': {
            const content = widget.contentField ? ((getValue(widget.contentField) as string) ?? widget.content) : widget.content;
            return (
              <p
                key={widget.content}
                className={cn(
                  'text-on-surface-variant',
                  widget.size === 'caption' ? 'text-caption' : 'text-body-sm',
                )}
              >
                {content}
              </p>
            );
          }

          case 'row': {
            return (
              <div
                key={`row-${idx}`}
                className="flex items-center gap-2"
                style={{ gap: widget.gap != null ? `${widget.gap * 4}px` : undefined }}
              >
                {widget.children.map((w, i) => renderWidget(w, i))}
              </div>
            );
          }

          case 'column': {
            return (
              <div
                key={`col-${idx}`}
                className="flex flex-col"
                style={{ gap: widget.gap != null ? `${widget.gap * 4}px` : '12px' }}
              >
                {widget.children.map((w, i) => renderWidget(w, i))}
              </div>
            );
          }

          case 'group': {
            return (
              <div key={`group-${idx}`} className="rounded-cw-sm border border-outline-variant/20 p-3 space-y-3">
                {widget.title && <h4 className="text-label font-medium text-on-surface-variant">{widget.title}</h4>}
                {widget.children.map((w, i) => renderWidget(w, i))}
              </div>
            );
          }

          default:
            return null;
        }
      },
    [getValue, handleAction, setUIState],
  );

  return <div className="space-y-3">{layout.widgets.map((w, i) => renderWidget(w, i))}</div>;
}
