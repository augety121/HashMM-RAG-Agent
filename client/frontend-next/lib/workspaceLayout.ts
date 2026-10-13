export const INSPECTOR_MIN_WIDTH = 320;
export const INSPECTOR_MAX_WIDTH = 900;
export const CHAT_MIN_WIDTH = 440;
export const SIDEBAR_EXPANDED_WIDTH = 260;
export const SIDEBAR_COLLAPSED_WIDTH = 52;
export const INSPECTOR_DIVIDER_WIDTH = 3;
export const COMPOSER_MIN_HEIGHT = 32;
export const COMPOSER_MAX_HEIGHT = 120;

export function clampInspectorWidth(requested: number, viewportWidth: number, sidebarOpen: boolean): number {
  const desired = Number.isFinite(requested) ? requested : 480;
  const viewport = Math.max(0, Number.isFinite(viewportWidth) ? viewportWidth : 0);
  const sidebar = sidebarOpen ? SIDEBAR_EXPANDED_WIDTH : SIDEBAR_COLLAPSED_WIDTH;
  const available = viewport - sidebar - CHAT_MIN_WIDTH - INSPECTOR_DIVIDER_WIDTH;
  const maximum = Math.max(INSPECTOR_MIN_WIDTH, Math.min(INSPECTOR_MAX_WIDTH, available));
  return Math.round(Math.min(maximum, Math.max(INSPECTOR_MIN_WIDTH, desired)));
}

export function composerTextareaHeight(text: string, scrollHeight: number): number {
  if (!text) return COMPOSER_MIN_HEIGHT;
  const measured = Number.isFinite(scrollHeight) ? scrollHeight : COMPOSER_MIN_HEIGHT;
  return Math.round(Math.min(COMPOSER_MAX_HEIGHT, Math.max(COMPOSER_MIN_HEIGHT, measured)));
}
