/**
 * Notification system v21 — browser notifications for long-running tasks.
 */

let permissionGranted = false;

export async function requestNotificationPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false;
  if (Notification.permission === 'granted') { permissionGranted = true; return true; }
  if (Notification.permission === 'denied') return false;
  const result = await Notification.requestPermission();
  permissionGranted = result === 'granted';
  return permissionGranted;
}

export function notify(title: string, body: string, icon?: string) {
  if (!permissionGranted) return;
  try {
    new Notification(title, { body, icon: icon || '/favicon.ico', tag: 'hashmm' });
  } catch {}
}

export function notifyTaskComplete(taskType: string, detail: string) {
  const titles: Record<string, string> = {
    code_task: '代码生成完成',
    doc_task: '文档生成完成',
    knowledge_task: '回答已生成',
  };
  notify(titles[taskType] || '任务完成', detail);
}
