import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V364 task-scoped desktop approvals", () => {
  const frontend = path.resolve(__dirname, "..");
  const repo = path.resolve(frontend, "..");

  it("binds desktop checkpoints and approvals to the active Chat", () => {
    const app = fs.readFileSync(path.join(frontend, "components", "App.tsx"), "utf8");
    const prompt = fs.readFileSync(path.join(repo, "desktop", "modules", "desktop-prompt.js"), "utf8");
    const main = fs.readFileSync(path.join(repo, "desktop", "main.js"), "utf8");
    expect(app).toContain('getCheckpoint()?.setTask(sid || "session")');
    expect(prompt).toContain('taskId: _text(src.taskId, 160) || "session"');
    expect(main).toContain('taskId: spec && spec.taskId ? spec.taskId : _ckptTaskId');
    expect(main).toContain('source: "browser-use"');
    expect(main).toContain('source: "computer-use"');
    expect(main).toContain('const executionTaskId = String(taskId || _ckptTaskId || "session")');
    expect(main).toContain('execTool: ({ name, args }) => cuExecOnce({ name, args, taskId: loopTaskId })');
    expect(app).toContain('getCheckpoint()?.setTask(sid || "session")');
  });

  it("keeps dismissed approvals pending and exposes them in the unified Run inspector", () => {
    const modal = fs.readFileSync(path.join(frontend, "components", "DesktopPromptModal.tsx"), "utf8");
    const center = fs.readFileSync(path.join(frontend, "components", "DesktopApprovalCenter.tsx"), "utf8");
    const panel = fs.readFileSync(path.join(frontend, "components", "RightContextPanel.tsx"), "utf8");
    expect(modal).toContain('const defer = () => setPrompt(null)');
    expect(modal).toContain('event.key === "Escape") { event.preventDefault(); defer();');
    expect(modal).toContain('aria-label="稍后处理"');
    expect(center).toContain("presentDesktopPrompt");
    expect(center).toContain("等待前一项确认后自动继续");
    expect(panel).toContain("<DesktopApprovalCenter");
    expect(panel).toContain("项桌面操作等待确认");
  });

  it("persists a bounded audit trail behind sender-guarded IPC", () => {
    const main = fs.readFileSync(path.join(repo, "desktop", "main.js"), "utf8");
    const preload = fs.readFileSync(path.join(repo, "desktop", "preload.js"), "utf8");
    const guard = fs.readFileSync(path.join(repo, "desktop", "modules", "ipc-guard.js"), "utf8");
    const journal = fs.readFileSync(path.join(repo, "desktop", "modules", "approval-journal.js"), "utf8");
    expect(main).toContain('"desktop-approvals.json"');
    expect(main).toContain('interruptPending("app-restarted")');
    expect(main).toContain('ipcMain.handle("app:desktopPromptList"');
    expect(preload).toContain('ipcRenderer.on("app:desktopPromptChanged"');
    expect(preload).toContain('ipcRenderer.invoke("app:desktopPromptList"');
    expect(guard).toContain('"app:desktopPromptPresent"');
    expect(guard).toContain('"ckpt:"');
    expect(journal).toContain('const SCHEMA = "hashmm.desktop-approval.v1"');
    expect(journal).toContain("this.items.slice(-this.limit)");
  });
});
