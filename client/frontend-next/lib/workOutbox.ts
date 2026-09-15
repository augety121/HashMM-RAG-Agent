/** Durable, owner-scoped outbox for work-control commands.
 *
 * Only command metadata is stored.  Prompts, tool arguments and file bodies
 * are never written here.  The server remains the source of truth; this queue
 * only preserves a user's explicit control intent across a short offline gap.
 */

export const WORK_OUTBOX_SCHEMA = "hashmm.work-outbox.v1";
const STORAGE_KEY = "hmm_work_outbox_v1";
const MAX_ITEMS = 64;

export interface WorkOutboxEntry {
  schema: typeof WORK_OUTBOX_SCHEMA;
  commandId: string;
  runId: string;
  ownerId: string;
  action: string;
  expectedRevision: number;
  createdAt: number;
  attempts: number;
}

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try { return window.localStorage; } catch { return null; }
}

function read(): WorkOutboxEntry[] {
  const store = storage();
  if (!store) return [];
  try {
    const parsed = JSON.parse(store.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is WorkOutboxEntry =>
      item && item.schema === WORK_OUTBOX_SCHEMA
      && typeof item.commandId === "string"
      && typeof item.runId === "string"
      && typeof item.ownerId === "string"
      && typeof item.action === "string"
      && Number.isInteger(item.expectedRevision)
      && item.expectedRevision >= 1,
    ).slice(-MAX_ITEMS);
  } catch { return []; }
}

function write(items: WorkOutboxEntry[]): void {
  const store = storage();
  if (!store) return;
  try { store.setItem(STORAGE_KEY, JSON.stringify(items.slice(-MAX_ITEMS))); } catch { /* quota/offline */ }
}

export function listWorkOutbox(ownerId = ""): WorkOutboxEntry[] {
  const owner = String(ownerId || "");
  return read().filter(item => !owner || item.ownerId === owner);
}

export function enqueueWorkCommand(input: Omit<WorkOutboxEntry, "schema" | "createdAt" | "attempts">): WorkOutboxEntry {
  const entry: WorkOutboxEntry = {
    schema: WORK_OUTBOX_SCHEMA,
    commandId: String(input.commandId || "").slice(0, 120),
    runId: String(input.runId || "").slice(0, 96),
    ownerId: String(input.ownerId || "").slice(0, 160),
    action: String(input.action || "").slice(0, 32),
    expectedRevision: Math.max(1, Number(input.expectedRevision || 1)),
    createdAt: Date.now(),
    attempts: 0,
  };
  if (!entry.commandId || !entry.runId || !entry.ownerId) return entry;
  const items = read();
  const existing = items.findIndex(item =>
    item.ownerId === entry.ownerId && item.commandId === entry.commandId,
  );
  if (existing >= 0) items[existing] = { ...items[existing], expectedRevision: entry.expectedRevision };
  else items.push(entry);
  write(items);
  return existing >= 0 ? items[existing] : entry;
}

export function removeWorkCommand(ownerId: string, commandId: string): void {
  write(read().filter(item => !(item.ownerId === ownerId && item.commandId === commandId)));
}

export interface WorkOutboxDrainResult {
  schema: typeof WORK_OUTBOX_SCHEMA;
  sent: number;
  pending: number;
  stoppedOnNetworkError: boolean;
}

export async function drainWorkOutbox(
  ownerId: string,
  send: (entry: WorkOutboxEntry) => Promise<{ ok?: boolean; duplicate?: boolean }>,
): Promise<WorkOutboxDrainResult> {
  let sent = 0;
  let stoppedOnNetworkError = false;
  for (const entry of listWorkOutbox(ownerId)) {
    try {
      const result = await send(entry);
      if (result?.ok || result?.duplicate) {
        removeWorkCommand(ownerId, entry.commandId);
        sent += 1;
      } else {
        // Revision conflicts and permission failures are terminal for this
        // exact command; never spin/replay them in the background.
        removeWorkCommand(ownerId, entry.commandId);
      }
    } catch (error) {
      const message = String((error as Error)?.message || error || "").toLowerCase();
      if (message.includes("failed to fetch") || message.includes("network")
          || message.includes("offline") || message.includes("fetch failed")) {
        stoppedOnNetworkError = true;
        break;
      }
      removeWorkCommand(ownerId, entry.commandId);
    }
  }
  return {
    schema: WORK_OUTBOX_SCHEMA,
    sent,
    pending: listWorkOutbox(ownerId).length,
    stoppedOnNetworkError,
  };
}

