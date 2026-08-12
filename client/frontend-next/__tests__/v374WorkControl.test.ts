import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");
const app = path.resolve(root, "..", "..", "app", "app", "src");

describe("V374 durable work control", () => {
  it("sends revision-bound idempotent commands from the desktop", () => {
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    const types = fs.readFileSync(path.join(root, "lib", "types.ts"), "utf8");
    const panel = fs.readFileSync(path.join(root, "components", "RightContextPanel.tsx"), "utf8");
    expect(api).toContain("/commands");
    expect(api).toContain("command_id: commandId, action, expected_revision: expectedRevision");
    expect(types).toContain('schema: "hashmm.work-control.v1"');
    expect(types).toContain("side_effect_boundary");
    expect(panel).toContain("activeWork.control.available_actions.map");
    expect(panel).toContain("系统不会自动重放");
  });

  it("uses the same command contract in the native App", () => {
    const repository = fs.readFileSync(path.join(app, "main", "java", "com", "hashmm", "app", "data", "remote", "WorkRuntimeRepository.kt"), "utf8");
    const screen = fs.readFileSync(path.join(app, "main", "java", "com", "hashmm", "app", "ui", "workbench", "RunsScreen.kt"), "utf8");
    expect(repository).toContain("/api/work-runs/$cleanId/commands");
    expect(repository).toContain('.put("expected_revision", expectedRevision)');
    expect(repository).toContain("MobileWorkOutboxItem");
    expect(repository).toContain('.put("command_id", commandId)');
    expect(repository).toContain("deferOutbox(uid, commandId)");
    expect(repository).toContain("reconcileOutbox");
    expect(screen).toContain("run.control.availableActions");
    expect(screen).toContain("重新派发为新任务");
  });
});
