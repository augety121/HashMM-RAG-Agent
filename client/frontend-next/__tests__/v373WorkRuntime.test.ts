import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V373 unified work runtime", () => {
  const frontend = path.resolve(__dirname, "..");
  const repo = path.resolve(frontend, "..");

  it("uses one incremental owner-bound feed for desktop work state", () => {
    const api = fs.readFileSync(path.join(frontend, "lib", "api.ts"), "utf8");
    const types = fs.readFileSync(path.join(frontend, "lib", "types.ts"), "utf8");
    const panel = fs.readFileSync(path.join(frontend, "components", "RightContextPanel.tsx"), "utf8");

    expect(types).toContain('schema: "hashmm.work-feed.v1"');
    expect(types).toContain("change_cursor: number");
    expect(types).toContain("event_cursor: number");
    expect(api).toContain("/api/work-runs?${q.toString()}");
    expect(api).toContain("after_cursor");
    expect(api).toContain("after_seq");
    expect(panel).toContain("统一工作账本");
    expect(panel).toContain("workRunsFeed(cursor, sid");
    expect(panel).toContain("workRunDetail(current.id, eventCursor)");
  });

  it("projects chat, long-loop and team execution into the same ledger", () => {
    const server = fs.readFileSync(path.join(repo, "hashmm", "api", "server.py"), "utf8");
    const loop = fs.readFileSync(path.join(repo, "hashmm", "agent", "loop_engine.py"), "utf8");
    const team = fs.readFileSync(path.join(repo, "hashmm", "agent", "team.py"), "utf8");
    const runtime = fs.readFileSync(path.join(repo, "hashmm", "agent", "work_runtime.py"), "utf8");

    expect(server).toContain("SSEProjector");
    expect(server).toContain('kind="chat"');
    expect(loop).toContain('kind="loop"');
    expect(team).toContain('kind="team"');
    expect(runtime).toContain('RUN_SCHEMA = "hashmm.work-run.v1"');
    expect(runtime).toContain("Token/thinking deltas are intentionally ignored");
    expect(runtime).toContain("arguments?");
  });

  it("keeps the App cache account-scoped, encrypted and server-authoritative", () => {
    const app = path.resolve(repo, "..", "app", "app", "src", "main", "java", "com", "hashmm", "app");
    const repository = fs.readFileSync(path.join(app, "data", "remote", "WorkRuntimeRepository.kt"), "utf8");
    const localStore = fs.readFileSync(path.join(app, "data", "cache", "LocalStore.kt"), "utf8");
    const secureCache = fs.readFileSync(path.join(app, "data", "cache", "SecureCache.kt"), "utf8");
    const workbench = fs.readFileSync(path.join(app, "ui", "workbench", "WorkbenchHubScreen.kt"), "utf8");

    expect(repository).toContain("/api/work-runs?after_cursor=");
    expect(repository).toContain("hashmm.mobile-work-cache.v1");
    expect(repository).toContain("The server is authoritative");
    expect(localStore).toContain("putWorkRuntimeSnapshot(userId");
    expect(secureCache).toContain("EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB");
    expect(workbench).toContain("WorkLedgerRow");
    expect(workbench).toContain("同一项工作，在手机查看，在电脑继续");
    expect(workbench).toContain("ui.workRuns");
  });
});
