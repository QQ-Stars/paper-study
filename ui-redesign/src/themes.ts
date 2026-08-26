/* 主题注册表：主题 = 对同一组设计令牌的整体覆盖。
 * tokens.css 中 :root 承载默认（书斋·昼）令牌；其余主题以
 * [data-theme='<id>'] 选择器重定义相同变量。组件只引用 var(--*)，
 * 新增主题只需：1) 在此注册；2) 在 tokens.css 增加一段变量覆盖。 */

export type ThemeId = 'light' | 'night';

export interface ThemeDefinition {
  id: ThemeId;
  label: string;
  description: string;
}

export const THEMES: ThemeDefinition[] = [
  { id: 'light', label: '书斋 · 昼', description: '宣纸底 · 墨字 · 朱砂点缀' },
  { id: 'night', label: '书斋 · 夜', description: '夜墨底 · 暖白字 · 亮朱砂' },
];

export const DEFAULT_THEME: ThemeId = 'light';

const STORAGE_KEY = 'paper-study-theme';

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return THEMES.some((theme) => theme.id === value);
}

export function readStoredTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isThemeId(stored) ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/* light 为默认令牌（无 data-theme），其余主题设置 data-theme 覆盖 */
export function applyTheme(id: ThemeId): void {
  const root = document.documentElement;
  if (id === DEFAULT_THEME) root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', id);
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* 隐私模式等场景下忽略持久化失败 */
  }
}
