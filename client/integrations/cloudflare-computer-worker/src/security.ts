export const MAX_JSON_BYTES = 256 * 1024;
export const MAX_FILE_BYTES = 8 * 1024 * 1024;
export const MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
export const MAX_ARGV = 64;
export const MAX_ARG_LENGTH = 4096;
export const MAX_PATH_LENGTH = 512;

const HANDLE = /^ws_[a-z2-7]{32,52}$/;
const ALLOWED_COMMANDS = new Set([
  "cat", "cut", "date", "dirname", "echo", "find", "grep",
  "head", "jq", "ls", "mkdir", "paste", "printf", "pwd", "sed",
  "sort", "tail", "touch", "tr", "uniq", "wc", "which",
]);

export function isWorkspaceHandle(value: string): boolean {
  return HANDLE.test(value);
}

export function workspacePath(value: string): string | null {
  if (!value || value.length > MAX_PATH_LENGTH || value.includes("\0") || value.includes("\\")) {
    return null;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return null;
  }
  if (decoded.startsWith("/") || decoded.split("/").some(part => part === ".." || part === "")) {
    return null;
  }
  const path = `/workspace/${decoded}`;
  return path === "/workspace" || path.startsWith("/workspace/") ? path : null;
}

export function validateArgv(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_ARGV) return null;
  if (!value.every(item => typeof item === "string" && item.length > 0 && item.length <= MAX_ARG_LENGTH && !item.includes("\0"))) {
    return null;
  }
  const argv = value as string[];
  if (!ALLOWED_COMMANDS.has(argv[0])) return null;
  return argv;
}

export function validateCwd(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return "/workspace";
  if (typeof value !== "string" || value.length > MAX_PATH_LENGTH || value.includes("\0") || value.includes("\\")) return null;
  if (value !== "/workspace" && !value.startsWith("/workspace/")) return null;
  if (value.split("/").includes("..")) return null;
  return value;
}

export function shellQuote(arg: string): string {
  if (/^[A-Za-z0-9_\-+=:,./@%]+$/.test(arg)) return arg;
  return `'${arg.replace(/'/g, "'\\''")}'`;
}

export function limitText(value: unknown, maxBytes = MAX_OUTPUT_BYTES): { text: string; truncated: boolean } {
  const text = typeof value === "string" ? value : value == null ? "" : String(value);
  const encoded = new TextEncoder().encode(text);
  if (encoded.byteLength <= maxBytes) return { text, truncated: false };
  return { text: new TextDecoder().decode(encoded.slice(0, maxBytes)), truncated: true };
}

export async function tokenMatches(provided: string, expected: string): Promise<boolean> {
  if (!provided || !expected) return false;
  const digest = async (value: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  const [left, right] = await Promise.all([digest(provided), digest(expected)]);
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (left[index % left.length] ?? 0) ^ (right[index % right.length] ?? 0);
  }
  return difference === 0;
}
