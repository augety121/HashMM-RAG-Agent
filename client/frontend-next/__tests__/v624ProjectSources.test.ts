import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const repo = path.resolve(__dirname, "..", "..");

describe("V624 project source folders stay local and owner scoped", () => {
  test("desktop exposes a narrow project folder bridge instead of generic filesystem access", () => {
    const preload = fs.readFileSync(path.join(repo, "desktop", "preload.js"), "utf8");
    const main = fs.readFileSync(path.join(repo, "desktop", "main.js"), "utf8");
    const guard = fs.readFileSync(path.join(repo, "desktop", "modules", "ipc-guard.js"), "utf8");
    expect(preload).toContain('contextBridge.exposeInMainWorld("hashmmProject"');
    expect(preload).toContain('ipcRenderer.invoke("project:pickSourceFolders")');
    expect(preload).toContain('ipcRenderer.invoke("project:activateSource", sourcePath)');
    expect(main).toContain('ipcMain.handle("project:pickSourceFolders"');
    expect(main).toContain('ipcMain.handle("project:activateSource"');
    expect(guard).toContain('"project:pickSourceFolders"');
    expect(guard).toContain('"project:activateSource"');
  });

  test("project source bindings use an account-scoped cache", () => {
    const cache = fs.readFileSync(path.join(repo, "frontend-next", "lib", "accountWorkspaceCache.ts"), "utf8");
    const view = fs.readFileSync(path.join(repo, "frontend-next", "components", "desktop", "WorkspaceShellView.tsx"), "utf8");
    expect(cache).toContain('"project-sources"');
    expect(cache).toContain("export function readProjectSources");
    expect(cache).toContain("export function writeProjectSources");
    expect(view).toContain("writeProjectSources(user, created.id, projectSourceFolders)");
    expect(view).toContain("getProjectDesktop()?.activateSource");
  });

  test("creation is one step and does not silently discard the created project", () => {
    const view = fs.readFileSync(path.join(repo, "frontend-next", "components", "desktop", "WorkspaceShellView.tsx"), "utf8");
    expect(view).toContain("const nextProject = created.project");
    expect(view).toContain("setSelectedProjectId(nextProject.id)");
    expect(view).toContain("useStore.getState().newChat()");
    expect(view).toContain("创建项目");
    expect(view).not.toContain("projectStep");
  });

  test("project rows return to project Chat while management remains a separate explicit entry", () => {
    const sidebar = fs.readFileSync(path.join(repo, "frontend-next", "components", "Sidebar.tsx"), "utf8");
    const chat = fs.readFileSync(path.join(repo, "frontend-next", "components", "ChatArea.tsx"), "utf8");
    expect(sidebar).toContain("readActiveProject");
    expect(sidebar).toContain("related[0].id");
    expect(sidebar).toContain("desktopView: null");
    expect(sidebar).toContain("useStore.getState().newChat()");
    expect(chat).toContain("projectContextId");
    expect(chat).toContain("项目详情");
  });

  test("library and device relay use progressive disclosure instead of permanent dense panels", () => {
    const view = fs.readFileSync(path.join(repo, "frontend-next", "components", "desktop", "WorkspaceShellView.tsx"), "utf8");
    const remote = fs.readFileSync(path.join(repo, "frontend-next", "components", "desktop", "RemoteView.tsx"), "utf8");
    const settings = fs.readFileSync(path.join(repo, "frontend-next", "components", "SettingsModal.tsx"), "utf8");
    expect(view).toContain("libraryDetailOpen");
    expect(view).toContain("setLibraryDetailOpen(true)");
    expect(remote).toContain('useState<"home" | "handoff" | "connect" | "device">("home")');
    expect(remote).toContain("接回 App 上的工作");
    expect(remote).toContain("我的设备");
    expect(remote).toContain("进入桌面");
    expect(settings).toContain("function WorkspaceSettingsTab()");
    expect(settings).toContain('{ id: "browser", label: "浏览器"');
  });
});
