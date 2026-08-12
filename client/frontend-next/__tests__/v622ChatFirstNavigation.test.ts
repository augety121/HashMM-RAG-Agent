import fs from "fs";
import path from "path";
import { describe, expect, it } from "vitest";

const frontend = path.resolve(__dirname, "..");
const read = (...parts: string[]) => fs.readFileSync(path.join(frontend, ...parts), "utf8");

describe("V622 Chat-first product navigation", () => {
  it("keeps internal canvas and personal pages out while exposing the durable Agent catalog", () => {
    const sidebar = read("components", "Sidebar.tsx");
    expect(sidebar).toContain('{ v: "work-active"');
    expect(sidebar).toContain('{ v: "hub-knowledge"');
    expect(sidebar).toContain('{ v: "plugins"');
    expect(sidebar).toContain('{ v: "remote"');
    expect(sidebar).not.toContain('{ v: "canvas-home"');
    expect(sidebar).toContain('{ v: "agents"');
    expect(sidebar).not.toContain('{ v: "personal"');
  });

  it("routes capability launchers back into the current Chat", () => {
    const chat = read("components", "ChatArea.tsx");
    expect(chat).toContain("<TeamPanel");
    expect(chat).toContain("setTeamOpen(true)");
    expect(chat).toContain("openCanvasMenu");
    expect(chat).toContain('className="relative"');
  });

  it("keeps legacy canvas and collaboration routes recoverable without making them a second Chat", () => {
    const canvas = read("components", "desktop", "CanvasHomeView.tsx");
    const agents = read("components", "desktop", "AgentsStudioView.tsx");
    expect(canvas).toContain("回到对话");
    expect(canvas).toContain('set({ desktopView: null })');
    expect(agents).toContain("回到对话");
    expect(agents).toContain('set({ desktopView: null })');
  });

  it("shows authoritative workspace counts instead of decorative status", () => {
    const workspace = read("components", "desktop", "WorkspaceShellView.tsx");
    expect(workspace).toContain("online_count");
    expect(workspace).toContain("recent_results.length");
    expect(workspace).toContain("实时同步");
  });

  it("exposes user-facing browser, computer, canvas, and collaboration controls in settings", () => {
    const settings = read("components", "SettingsModal.tsx");
    expect(settings).toContain('id: "capabilities"');
    expect(settings).toContain("hmm_cap_browser");
    expect(settings).toContain("hmm_cap_computer");
    expect(settings).toContain("hmm_cap_canvas");
    expect(settings).toContain("hmm_cap_team");
    expect(settings).toContain('role="switch"');
  });
});
