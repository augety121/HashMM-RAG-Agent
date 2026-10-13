/**
 * Keyboard shortcuts v20.
 */
type ShortcutHandler = () => void;

interface Shortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  handler: ShortcutHandler;
  description: string;
}

const shortcuts: Shortcut[] = [];

export function registerShortcut(s: Shortcut) {
  shortcuts.push(s);
}

export function initShortcuts() {
  document.addEventListener('keydown', (e) => {
    for (const s of shortcuts) {
      if (
        e.key.toLowerCase() === s.key.toLowerCase() &&
        !!e.ctrlKey === !!s.ctrl &&
        !!e.shiftKey === !!s.shift
      ) {
        e.preventDefault();
        s.handler();
        return;
      }
    }
  });
}

export function getShortcuts() {
  return shortcuts.map(s => ({
    key: `${s.ctrl ? 'Ctrl+' : ''}${s.shift ? 'Shift+' : ''}${s.key.toUpperCase()}`,
    description: s.description,
  }));
}
