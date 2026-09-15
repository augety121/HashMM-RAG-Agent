import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const frontend = process.cwd();
const read = (...parts: string[]) => fs.readFileSync(path.join(frontend, ...parts), "utf8");

describe("V600 user capability workspace", () => {
  it("turns the plugin page into a user work launcher while retaining runtime truth", () => {
    const plugin = read("components", "desktop", "PluginCenterView.tsx");
    expect(plugin).toContain("runtimeCapabilities()");
    expect(plugin).toContain('title: "电脑工作台"');
    expect(plugin).toContain('view: "workbench"');
    expect(plugin).toContain("常用工作区");
    expect(plugin).toContain("只展示已经审核并能在 Chat 中实际使用的插件");
    expect(plugin).toContain("<UserSkillsSettings />");
    expect(plugin).not.toContain("<span>真实入口</span>");
    expect(plugin).not.toContain("SHA-256 {plugin.sha256");
  });

  it("shows composer labels in a wide Chat container and hides only when Chat is narrow", () => {
    const css = read("app", "globals.css");
    expect(css).toContain(".composer-tools .composer-tool-label { display: inline; }");
    expect(css).toContain("@container chat-area (max-width: 620px)");
    expect(css).not.toContain("@container composer (max-width: 900px)");
  });

  it("moves the desktop workbench into plugins and renames the user device destination", () => {
    const sidebar = read("components", "Sidebar.tsx");
    const hub = read("components", "desktop", "WorkspaceHubView.tsx");
    const navigation = read("lib", "desktopNavigation.ts");
    expect(sidebar).toContain('label: "设备接力"');
    expect(sidebar).toContain('v === "remote" ? runnerDot');
    expect(hub).not.toContain('id: "workbench"');
    expect(navigation).toContain('workbench: "plugins"');
  });

  it("provides a real personal page and records App-to-desktop handoffs", () => {
    const personal = read("components", "desktop", "PersonalHomeView.tsx");
    const app = read("components", "App.tsx");
    const remote = read("components", "desktop", "RemoteView.tsx");
    const panel = read("components", "DesktopPanel.tsx");
    expect(personal).toContain('getWorkspaceSnapshot("personal"');
    expect(personal).toContain("我的工作");
    expect(personal).toContain("个人 Skills");
    expect(panel).toContain('<PersonalHomeView />');
    expect(app).toContain('localStorage.setItem("hmm_last_device_resume"');
    expect(app).toContain('"hmm-device-resume-open"');
    expect(app).not.toContain('localStorage.setItem("hmm_last_handoff"');
    expect(remote).toContain("接回 App 上的工作");
    expect(remote).toContain("lastHandoff.conv_id");
  });

  it("gives projects and library user actions instead of passive cards", () => {
    const workspace = read("components", "desktop", "WorkspaceShellView.tsx");
    expect(workspace).toContain("继续讨论");
    expect(workspace).toContain("每个项目保留目标、资料、对话、成果与验收标准");
    expect(workspace).toContain("和这些资料聊一聊");
    expect(workspace).toContain("帮我比较");
    expect(workspace).toContain("selectedDocuments");
  });
});
