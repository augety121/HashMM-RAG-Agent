import type { Session, User } from "./types";
import type { WorkProject } from "./api";
import { getDesktop } from "./desktop";

const SCHEMA = "hashmm.account-workspace-cache.v1";
const PREFIX = "hmm_account_workspace";
// Sidebar metadata is intentionally much larger than the message-body cache:
// keeping only 120 rows made older/project conversations disappear offline.
const MAX_SESSIONS = 10000;
const MAX_MESSAGE_BODIES = 80;
const MAX_PROJECTS = 100;

type Scope = "sessions" | "projects" | "active-project" | "project-sources";

type ProjectSources = Record<string, string[]>;

interface Envelope<T> {
  schema: typeof SCHEMA;
  subject: string;
  updatedAt: number;
  value: T;
}

interface SessionReplica {
  schema: "hashmm.account-workspace-replica.v1";
  sessions: Session[];
  saved_at: number;
}

async function replicaNamespace(user: User | null | undefined): Promise<string> {
  const subject = accountSubject(user);
  if (!subject || typeof crypto === "undefined" || !crypto.subtle) return "";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(subject));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

function desktopReplicaBridge() {
  const bridge = getDesktop();
  return bridge?.workCanvasCacheGet && bridge?.workCanvasCachePut ? bridge : null;
}

async function persistEncryptedSessions(user: User | null | undefined, sessions: Session[]): Promise<void> {
  const bridge = desktopReplicaBridge();
  if (!bridge) return;
  const namespace = await replicaNamespace(user);
  if (!namespace) return;
  const replica: SessionReplica = {
    schema: "hashmm.account-workspace-replica.v1",
    sessions,
    saved_at: Date.now(),
  };
  await bridge.workCanvasCachePut!(namespace, "account:sessions", String(replica.saved_at), replica);
}

export async function hydrateAccountSessions(user: User | null | undefined): Promise<Session[]> {
  const bridge = desktopReplicaBridge();
  if (!bridge) return [];
  const namespace = await replicaNamespace(user);
  if (!namespace) return [];
  const result = await bridge.workCanvasCacheGet!(namespace, "account:sessions");
  const data = result?.data as Partial<SessionReplica> | undefined;
  return result?.ok && result.hit && data?.schema === "hashmm.account-workspace-replica.v1" && Array.isArray(data.sessions)
    ? data.sessions.filter(isSession).slice(0, MAX_SESSIONS)
    : [];
}

function available(): boolean {
  return typeof window !== "undefined" && typeof localStorage !== "undefined";
}

export function accountSubject(user: User | null | undefined): string {
  const raw = String(user?.id || user?.username || "").trim();
  if (!raw) return "";
  return encodeURIComponent(raw).replace(/%/g, "_").slice(0, 180);
}

function key(scope: Scope, user: User | null | undefined): string {
  const subject = accountSubject(user);
  return subject ? `${PREFIX}:${subject}:${scope}` : "";
}

function readEnvelope<T>(scope: Scope, user: User | null | undefined): T | null {
  if (!available()) return null;
  const storageKey = key(scope, user);
  const subject = accountSubject(user);
  if (!storageKey || !subject) return null;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Envelope<T>>;
    if (parsed.schema !== SCHEMA || parsed.subject !== subject || !("value" in parsed)) return null;
    return parsed.value as T;
  } catch {
    return null;
  }
}

function writeEnvelope<T>(scope: Scope, user: User | null | undefined, value: T): void {
  if (!available()) return;
  const storageKey = key(scope, user);
  const subject = accountSubject(user);
  if (!storageKey || !subject) return;
  const envelope: Envelope<T> = { schema: SCHEMA, subject, updatedAt: Date.now(), value };
  localStorage.setItem(storageKey, JSON.stringify(envelope));
}

function isSession(value: unknown): value is Session {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<Session>;
  return typeof row.id === "string"
    && typeof row.title === "string"
    && typeof row.created === "number"
    && Array.isArray(row.messages);
}

/**
 * Conversation bodies are a local acceleration cache, not an authorization
 * source. The account-bound envelope prevents one signed-in account from
 * inheriting another account's sidebar while still allowing the same account
 * to resume after logout, re-authentication, or an offline desktop restart.
 */
export function readAccountSessions(user: User | null | undefined): Session[] {
  const scoped = readEnvelope<unknown>("sessions", user);
  if (Array.isArray(scoped)) return scoped.filter(isSession).slice(0, MAX_SESSIONS);

  // One-time migration from the legacy global cache. It is only claimed by an
  // identified account and immediately removed so it can never be replayed
  // into a second account.
  if (available() && accountSubject(user)) {
    try {
      const legacyRaw = localStorage.getItem("hmm_s");
      const legacy = legacyRaw ? JSON.parse(legacyRaw) : null;
      if (Array.isArray(legacy)) {
        const sessions = legacy.filter(isSession).slice(0, MAX_SESSIONS);
        writeAccountSessions(user, sessions);
        localStorage.removeItem("hmm_s");
        return sessions;
      }
    } catch {
      localStorage.removeItem("hmm_s");
    }
  }
  return [];
}

export function writeAccountSessions(user: User | null | undefined, sessions: Session[]): void {
  if (!accountSubject(user)) return;
  const ordered = [...sessions]
    .filter(isSession)
    .sort((a, b) => (b.content_activity_at || b.created) - (a.content_activity_at || a.created))
    .slice(0, MAX_SESSIONS)
    .map((session, index) => index < MAX_MESSAGE_BODIES
      ? session
      : { ...session, messages: [] });
  void persistEncryptedSessions(user, ordered).catch(() => {});
  // Desktop localStorage retains only the non-sensitive sidebar index. Full
  // message bodies live in the per-account safeStorage replica above.
  const localValue = desktopReplicaBridge()
    ? ordered.map(session => ({ ...session, messages: [] }))
    : ordered;
  try {
    writeEnvelope("sessions", user, localValue);
  } catch {
    // Quota fallback: retain the complete recent index and discard cached
    // message bodies. The server remains authoritative and reloads bodies on click.
    writeEnvelope("sessions", user, ordered.map(session => ({ ...session, messages: [] })));
  }
}

function isProject(value: unknown): value is WorkProject {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<WorkProject>;
  return typeof row.id === "string" && typeof row.name === "string";
}

export function readAccountProjects(user: User | null | undefined): WorkProject[] {
  const value = readEnvelope<unknown>("projects", user);
  return Array.isArray(value) ? value.filter(isProject).slice(0, MAX_PROJECTS) : [];
}

export function writeAccountProjects(user: User | null | undefined, projects: WorkProject[]): void {
  if (!accountSubject(user)) return;
  const ordered = [...projects]
    .filter(isProject)
    .sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0))
    .slice(0, MAX_PROJECTS);
  writeEnvelope("projects", user, ordered);
}

export function readActiveProject(user: User | null | undefined): string {
  const value = readEnvelope<unknown>("active-project", user);
  return typeof value === "string" ? value : "";
}

export function writeActiveProject(user: User | null | undefined, projectId: string): void {
  if (!accountSubject(user)) return;
  writeEnvelope("active-project", user, String(projectId || ""));
  if (available()) {
    window.dispatchEvent(new CustomEvent("hmm-project-selected", {
      detail: { projectId: String(projectId || "") },
    }));
  }
}

export function readProjectSources(
  user: User | null | undefined,
  projectId: string,
): string[] {
  const value = readEnvelope<unknown>("project-sources", user);
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const rows = (value as ProjectSources)[String(projectId || "")];
  return Array.isArray(rows)
    ? rows.map(item => String(item || "").trim()).filter(Boolean).slice(0, 8)
    : [];
}

export function writeProjectSources(
  user: User | null | undefined,
  projectId: string,
  paths: string[],
): void {
  if (!accountSubject(user) || !projectId) return;
  const current = readEnvelope<unknown>("project-sources", user);
  const next: ProjectSources = current && typeof current === "object" && !Array.isArray(current)
    ? { ...(current as ProjectSources) }
    : {};
  next[String(projectId)] = Array.from(new Set(
    paths.map(item => String(item || "").trim()).filter(Boolean),
  )).slice(0, 8);
  writeEnvelope("project-sources", user, next);
}

export function clearAccountWorkspaceCache(user: User | null | undefined): void {
  if (!available()) return;
  for (const scope of ["sessions", "projects", "active-project", "project-sources"] as const) {
    const storageKey = key(scope, user);
    if (storageKey) localStorage.removeItem(storageKey);
  }
}
