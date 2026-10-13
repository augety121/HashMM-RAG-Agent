/**
 * Simple global toast system — no React context needed.
 * Creates DOM elements directly for maximum compatibility.
 */

let container: HTMLDivElement | null = null;

function getContainer(): HTMLDivElement {
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
    document.body.appendChild(container);
  }
  return container;
}

// V86: emoji → 内联 SVG（stroke 继承文字色，与边框同色）
const _svg = (d: string) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">${d}</svg>`;
const ICONS: Record<string, string> = {
  success: _svg('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/>'),
  error: _svg('<circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/>'),
  info: _svg('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'),
  warning: _svg('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>'),
};
const COLORS: Record<string, { bg: string; border: string }> = {
  success: { bg: 'rgba(34,197,94,0.12)', border: '#22c55e' },
  error: { bg: 'rgba(239,68,68,0.12)', border: '#ef4444' },
  info: { bg: 'rgba(56,111,238,0.12)', border: '#386fee' },
  warning: { bg: 'rgba(245,158,11,0.12)', border: '#f59e0b' },
};

export function showToast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'info', duration = 3000) {
  const c = getContainer();
  const el = document.createElement('div');
  const color = COLORS[type] || COLORS.info;
  el.style.cssText = `
    display:flex;align-items:center;gap:8px;padding:10px 16px;border-radius:10px;
    font-size:13px;pointer-events:auto;cursor:pointer;min-width:200px;max-width:400px;
    background:${color.bg};border:1px solid ${color.border};color:${color.border};
    box-shadow:0 4px 12px rgba(0,0,0,0.1);backdrop-filter:blur(8px);
    animation:slideUp 0.25s ease;
  `;
  el.innerHTML = `<span>${ICONS[type] || ''}</span><span style="flex:1">${message}</span>`;
  el.onclick = () => el.remove();
  c.appendChild(el);

  if (duration > 0) {
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      el.style.transition = 'all 0.2s';
      setTimeout(() => el.remove(), 200);
    }, duration);
  }
}

// Make globally available
if (typeof window !== 'undefined') {
  (window as any).showToast = showToast;
}
