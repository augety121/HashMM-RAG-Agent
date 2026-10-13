import { DurableObject } from "cloudflare:workers";
import {
  type DurableObjectStorageLike,
  getWorkspace,
  WorkspaceServiceProxy,
  withWorkspace,
} from "@cloudflare/computer";
import { WorkerBackend, type WorkerBackendOptions } from "@cloudflare/computer/backends/worker";
import {
  MAX_FILE_BYTES,
  MAX_JSON_BYTES,
  isWorkspaceHandle,
  limitText,
  shellQuote,
  tokenMatches,
  validateArgv,
  validateCwd,
  workspacePath,
} from "./security";

interface Env {
  HASHMM_WORKSPACE: DurableObjectNamespace<HashMMWorkspace>;
  HASHMM_GATEWAY_TOKEN: string;
  LOADER: NonNullable<WorkerBackendOptions["loader"]>;
}

interface ExecRequest { argv?: unknown; cwd?: unknown }

export { WorkspaceServiceProxy };

export class HashMMWorkspace extends withWorkspace(class extends DurableObject<Env> {}, self => {
  const { ctx, env } = self as unknown as { ctx: DurableObjectState; env: Env };
  return {
    storage: ctx.storage as unknown as DurableObjectStorageLike,
    backends: [new WorkerBackend({
      loader: env.LOADER,
      workspace: { binding: "HASHMM_WORKSPACE", id: ctx.id.toString() },
      ctx,
    })],
  };
}) {}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/v1/health" && request.method === "GET") {
      return json({
        protocol: "hashmm-cloud-workspace/1.0",
        provider: "cloudflare_computer_worker_shell",
        upstream: "@cloudflare/computer@0.1.0-alpha.1",
        preview: true,
        ready: Boolean(env.HASHMM_GATEWAY_TOKEN),
      }, 200);
    }
    if (!await authorized(request, env)) return json({ error: "not_found" }, 404);

    const execMatch = url.pathname.match(/^\/v1\/workspaces\/([^/]+)\/exec\/?$/);
    if (execMatch) return handleExec(request, env, execMatch[1]);
    const fileMatch = url.pathname.match(/^\/v1\/workspaces\/([^/]+)\/files\/(.+)$/);
    if (fileMatch) return handleFile(request, env, fileMatch[1], fileMatch[2]);
    return json({ error: "not_found" }, 404);
  },
} satisfies ExportedHandler<Env>;

async function authorized(request: Request, env: Env): Promise<boolean> {
  const header = request.headers.get("authorization") || "";
  const provided = header.startsWith("Bearer ") ? header.slice(7) : "";
  return tokenMatches(provided, env.HASHMM_GATEWAY_TOKEN || "");
}

function stubFor(env: Env, handle: string) {
  return env.HASHMM_WORKSPACE.get(env.HASHMM_WORKSPACE.idFromName(handle));
}

async function handleExec(request: Request, env: Env, handle: string): Promise<Response> {
  if (request.method !== "POST") return method("POST");
  if (!isWorkspaceHandle(handle)) return json({ error: "not_found" }, 404);
  const length = Number(request.headers.get("content-length") || "0");
  if (length > MAX_JSON_BYTES) return json({ error: "request_too_large" }, 413);
  let raw: string;
  try { raw = await request.text(); } catch { return json({ error: "invalid_body" }, 400); }
  if (new TextEncoder().encode(raw).byteLength > MAX_JSON_BYTES) return json({ error: "request_too_large" }, 413);
  let body: ExecRequest;
  try { body = JSON.parse(raw) as ExecRequest; } catch { return json({ error: "invalid_json" }, 400); }
  const argv = validateArgv(body.argv);
  const cwd = validateCwd(body.cwd);
  if (!argv) return json({ error: "invalid_or_unsupported_argv" }, 422);
  if (!cwd) return json({ error: "invalid_cwd" }, 422);
  try {
    const workspace = await getWorkspace(stubFor(env, handle) as unknown as Parameters<typeof getWorkspace>[0]);
    const execution = await workspace.shell.exec(argv.map(shellQuote).join(" "), { cwd, encoding: "utf8" });
    const result = await execution.result() as Record<string, unknown>;
    const stdout = limitText(result.stdout);
    const stderr = limitText(result.stderr);
    return json({
      exitCode: Number(result.exitCode ?? result.exit_code ?? 0),
      stdout: stdout.text,
      stderr: stderr.text,
      truncated: stdout.truncated || stderr.truncated,
    }, 200);
  } catch (error) {
    return json({ error: "execution_failed", code: safeCode(error) }, 502);
  }
}

async function handleFile(request: Request, env: Env, handle: string, rawPath: string): Promise<Response> {
  if (!isWorkspaceHandle(handle)) return json({ error: "not_found" }, 404);
  const path = workspacePath(rawPath);
  if (!path) return json({ error: "invalid_path" }, 422);
  const workspace = await getWorkspace(stubFor(env, handle) as unknown as Parameters<typeof getWorkspace>[0]);
  if (request.method === "PUT") {
    const length = Number(request.headers.get("content-length") || "0");
    if (length > MAX_FILE_BYTES) return json({ error: "file_too_large" }, 413);
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.byteLength > MAX_FILE_BYTES) return json({ error: "file_too_large" }, 413);
    try { await workspace.fs.writeFile(path, bytes); return new Response(null, { status: 204 }); }
    catch (error) { return json({ error: "write_failed", code: safeCode(error) }, 502); }
  }
  if (request.method === "GET") {
    try {
      const stream = await workspace.fs.readFile(path, {});
      return new Response(stream, { status: 200, headers: { "content-type": "application/octet-stream" } });
    } catch (error) {
      return json({ error: safeCode(error) === "ENOENT" ? "not_found" : "read_failed" }, safeCode(error) === "ENOENT" ? 404 : 502);
    }
  }
  return method("GET, PUT");
}

function safeCode(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" && /^[A-Z0-9_]{1,40}$/.test(code) ? code : "UPSTREAM_ERROR";
}

function method(allow: string): Response {
  return new Response(JSON.stringify({ error: "method_not_allowed" }), { status: 405, headers: { allow, "content-type": "application/json" } });
}

function json(payload: unknown, status: number): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
