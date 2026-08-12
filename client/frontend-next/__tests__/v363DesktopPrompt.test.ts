import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V363 first-party desktop approvals", () => {
  const frontend = path.resolve(__dirname, "..");
  const repo = path.resolve(frontend, "..");

  it("renders desktop approvals inside the HashMM design system", () => {
    const component = fs.readFileSync(path.join(frontend, "components", "DesktopPromptModal.tsx"), "utf8");
    const app = fs.readFileSync(path.join(frontend, "components", "App.tsx"), "utf8");
    expect(component).toContain('role="alertdialog"');
    expect(component).toContain("prompt.boundary");
    expect(component).toContain("prompt.cancelId");
    expect(component).toContain("defaultButton.current?.focus()");
    expect(app).toContain("<DesktopPromptModal />");
  });

  it("keeps system message boxes only for the renderer-independent crash fallback", () => {
    const main = fs.readFileSync(path.join(repo, "desktop", "main.js"), "utf8");
    expect(main).not.toContain("dialog.showMessageBox(mainWindow");
    expect(main).not.toContain("dialog.showMessageBox({");
    expect((main.match(/showMessageBoxSync/g) || [])).toHaveLength(1);
    expect(main).toContain('requestDesktopPrompt({');
    expect(main).not.toContain("无对话环境则直接执行");
  });

  it("uses a narrow, sender-guarded request/decision bridge", () => {
    const preload = fs.readFileSync(path.join(repo, "desktop", "preload.js"), "utf8");
    const guard = fs.readFileSync(path.join(repo, "desktop", "modules", "ipc-guard.js"), "utf8");
    expect(preload).toContain('ipcRenderer.on("app:desktopPrompt"');
    expect(preload).toContain('ipcRenderer.send("app:desktopPromptReady"');
    expect(preload).toContain('ipcRenderer.send("app:desktopPromptDecision"');
    expect(guard).toContain('"app:desktopPromptDecision"');
  });
});
