/**
 * Plugin UI JSON Layout Language — Type Definitions
 *
 * 插件开发者使用声明式 JSON 描述前端 UI，无需编写 React 代码。
 * 后端 /api/plugin/{plugin_id}/ui 返回 UILayout，前端 PluginLayoutRenderer 渲染。
 */

export interface UILayout {
  title?: string;
  widgets: UIWidget[];
}

export type UIWidget =
  | UITextarea
  | UIButton
  | UIImage
  | UISpinner
  | UIAlert
  | UIText
  | UIRow
  | UIColumn
  | UIGroup;

export interface UIWidgetBase {
  key?: string;
  visibleWhen?: string;
}

export interface UITextarea extends UIWidgetBase {
  type: 'textarea';
  label?: string;
  placeholder?: string;
  rows?: number;
  key: string;
  /** Default value */
  defaultValue?: string;
}

export interface UIButton extends UIWidgetBase {
  type: 'button';
  label: string;
  variant?: 'default' | 'primary' | 'outline';
  /** Disabled when this state key is truthy */
  disabledWhen?: string;
  action: UIAction;
}

export interface UIImage extends UIWidgetBase {
  type: 'image';
  /** State key that holds the image URL */
  sourceField: string;
  /** Alt text */
  alt?: string;
}

export interface UISpinner extends UIWidgetBase {
  type: 'spinner';
  /** Spinner label text */
  label?: string;
}

export interface UIAlert extends UIWidgetBase {
  type: 'alert';
  /** severity: 'error' | 'info' | 'success' */
  severity: 'error' | 'info' | 'success';
  /** State key holding the message text */
  textField: string;
}

export interface UIText extends UIWidgetBase {
  type: 'text';
  content: string;
  /** State key to bind dynamic content */
  contentField?: string;
  /** text size: 'caption' | 'body' */
  size?: 'caption' | 'body';
}

export interface UIRow extends UIWidgetBase {
  type: 'row';
  children: UIWidget[];
  gap?: number;
}

export interface UIColumn extends UIWidgetBase {
  type: 'column';
  children: UIWidget[];
  gap?: number;
}

export interface UIGroup extends UIWidgetBase {
  type: 'group';
  title?: string;
  children: UIWidget[];
}

/** Action triggered by button click */
export interface UIAction {
  /** API endpoint (relative path like /api/tool/execute) */
  endpoint: string;
  method: 'POST' | 'GET';
  /** Request body. Use "${key}" syntax to interpolate input values */
  body?: Record<string, unknown>;
  /** On success, extract these fields from response data and store in state */
  resultMap?: Record<string, string>;
  /** State key to set on loading start */
  loadingKey?: string;
  /** State key to set on success (boolean) */
  successKey?: string;
  /** State key to set on error (stores error message) */
  errorKey?: string;
}

/** Runtime state managed by the renderer */
export interface UIRenderState {
  [key: string]: unknown;
}
