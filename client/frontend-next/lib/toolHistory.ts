/**
 * Build a bounded, chronological conversation excerpt for a local tool loop.
 *
 * Computer Use runs in the renderer and therefore cannot rely on the backend's
 * conversation compactor. This helper deliberately only forwards user and
 * assistant text, keeps the first user goal when possible, and marks every
 * truncation. It never invents a summary, which keeps tool decisions auditable
 * for long conversations.
 */
export interface ToolHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ToolHistoryOptions {
  maxMessages?: number;
  maxChars?: number;
  maxMessageChars?: number;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try { return JSON.stringify(value); } catch { return String(value); }
}

function clip(text: string, limit: number): string {
  const value = text.trim();
  if (value.length <= limit) return value;
  if (limit <= 40) return value.slice(0, limit);
  // Keep both the beginning (usually the request/intent) and the end (usually
  // the latest result), and make the omission explicit to the model.
  const marker = "\n[…中间内容已裁剪…]\n";
  const tail = Math.min(Math.max(20, Math.floor(limit * 0.22)), Math.max(20, limit - marker.length - 1));
  const head = Math.max(0, limit - tail - marker.length);
  return `${value.slice(0, head)}${marker}${value.slice(-tail)}`.slice(0, limit);
}

export function buildToolHistory(
  messages: Array<{ role?: string; content?: unknown }>,
  options: ToolHistoryOptions = {},
): ToolHistoryMessage[] {
  const maxMessages = Math.max(2, Math.floor(options.maxMessages ?? 16));
  const maxChars = Math.max(512, Math.floor(options.maxChars ?? 12000));
  const maxMessageChars = Math.max(128, Math.floor(options.maxMessageChars ?? 1800));
  const source = messages
    .filter(m => (m.role === "user" || m.role === "assistant") && asText(m.content).trim())
    .map(m => ({ role: m.role as "user" | "assistant", content: clip(asText(m.content), maxMessageChars) }));
  if (!source.length) return [];

  const picked: ToolHistoryMessage[] = [];
  let chars = 0;
  let firstGoalIncluded = false;
  const firstUser = source.find(m => m.role === "user");
  for (let i = source.length - 1; i >= 0 && picked.length < maxMessages; i--) {
    const item = source[i];
    const room = maxChars - chars;
    if (room <= 0) break;
    const content = clip(item.content, room);
    picked.push({ ...item, content });
    if (item === firstUser) firstGoalIncluded = true;
    chars += content.length;
    if (content.length < item.content.length) break;
  }

  // A long conversation can otherwise lose the original user goal. Add it at
  // the front only when it is not already in the selected window and budget
  // permits; the result remains a faithful excerpt, not a generated summary.
  if (firstUser && !firstGoalIncluded) {
    // If the count is full, replace the oldest selected turn. The original
    // goal is more useful to a tool loop than an arbitrary middle reply.
    if (picked.length >= maxMessages) {
      const oldest = picked[picked.length - 1];
      const room = maxChars - (chars - oldest.content.length);
      if (room >= 128) {
        picked.pop();
        picked.push({ ...firstUser, content: clip(firstUser.content, Math.min(maxMessageChars, room)) });
      }
    } else {
      const room = maxChars - chars;
      if (room >= 128) {
        picked.push({ ...firstUser, content: clip(firstUser.content, Math.min(maxMessageChars, room)) });
      }
    }
  }
  return picked.reverse();
}
