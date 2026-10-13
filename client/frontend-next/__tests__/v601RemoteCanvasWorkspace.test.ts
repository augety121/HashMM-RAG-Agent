import fs from "fs";
import path from "path";
import { describe, expect, it } from "vitest";

const frontend = path.resolve(__dirname, "..");
const root = path.resolve(frontend, "..");
const read = (...parts: string[]) => fs.readFileSync(path.join(root, ...parts), "utf8");

describe("V601 user workspaces and private-network remote bridge", () => {
  it("keeps Canvas in Chat while exposing the durable Agent catalog", () => {
    const sidebar = read("frontend-next", "components", "Sidebar.tsx");
    const panel = read("frontend-next", "components", "DesktopPanel.tsx");
    const chat = read("frontend-next", "components", "ChatArea.tsx");
    expect(sidebar).not.toContain('{ v: "canvas-home"');
    expect(sidebar).toContain('{ v: "agents"');
    expect(sidebar).not.toContain('{ v: "personal"');
    expect(sidebar).toContain('{ v: "plugins"');
    expect(panel).toContain('view === "canvas-home"');
    expect(panel).toContain("<CanvasHomeView />");
    expect(chat).toContain("<TeamPanel");
    expect(chat).toContain("setTeamOpen(true)");
    expect(chat).toContain("openCanvasMenu");
    expect(chat).toContain('className="relative"');
  });

  it("creates real conversation-owned canvas files instead of a demo page", () => {
    const canvas = read("frontend-next", "components", "desktop", "CanvasHomeView.tsx");
    expect(canvas).toContain("createConversation");
    expect(canvas).toContain("saveConvFile");
    expect(canvas).toContain("listConvFiles");
    expect(canvas).toContain("openArtifact");
    expect(canvas).toContain("服务器暂时不可用，已打开本地草稿");
  });

  it("exposes a narrow bridge for overlay discovery and explicit clients", () => {
    const preload = read("desktop", "preload.js");
    const main = read("desktop", "main.js");
    const service = read("desktop", "services", "remote-network-bridge.js");
    expect(preload).toContain('ipcRenderer.invoke("remote:networkBridgeStatus")');
    expect(preload).toContain('ipcRenderer.invoke("remote:launchRdp"');
    expect(preload).toContain('ipcRenderer.invoke("remote:launchMoonlight"');
    expect(main).toContain('ipcMain.handle("remote:openDirectViewer"');
    expect(service).toContain("shell: false");
    expect(service).toContain("HashMM 不读取或保存组网密钥");
  });

  it("turns Library into a real multi-document selection workflow", () => {
    const workspace = read("frontend-next", "components", "desktop", "WorkspaceShellView.tsx");
    expect(workspace).toContain("selectedDocuments");
    expect(workspace).toContain("startWithDocuments");
    expect(workspace).toContain("接下来想做什么");
    expect(workspace).toContain("为关键结论保留可以核对的出处");
  });
});
