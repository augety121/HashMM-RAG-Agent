import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const root = process.cwd();
const read = (file: string) => fs.readFileSync(path.join(root, file), "utf8");

describe("V699 user-facing projects, publisher and automation", () => {
  test("projects are created as local Chat work instead of a detached admin view", () => {
    const sidebar = read("components/Sidebar.tsx");
    expect(sidebar).toContain("createWorkProject");
    expect(sidebar).toContain("projectComposerOpen");
    expect(sidebar).toContain("projectSources");
    expect(sidebar).toContain("创建项目");
    expect(sidebar).toContain("startProjectChat(project)");
    expect(sidebar).toContain("中新建对话");
    expect(sidebar).toContain("item.project_id === project.id");
    expect(sidebar).not.toContain("ungroupedRecent");
    expect(sidebar).toContain('const unassignedRecent = filtered.filter(item => !String(item.project_id || "").trim())');
    expect(sidebar).toContain("const recentItems = unassignedRecent.slice(0, recentLimit)");
    expect(sidebar).toContain("writeActiveProject(user, projectId)");
    expect(sidebar).toContain("加载更早对话");
  });

  test("publisher studio and AI radar are reachable from user surfaces", () => {
    const plugins = read("components/desktop/PluginCenterView.tsx");
    const scheduled = read("components/desktop/ScheduledView.tsx");
    expect(plugins).toContain("publisher_studio");
    expect(plugins).toContain("AI 公众号简报工作室");
    expect(plugins).toContain("publisher-work");
    expect(scheduled).toContain("ai_news_radar");
    expect(scheduled).toContain("AI 热点候选雷达");
  });

  test("project work returns to Chat rather than opening the legacy workspace", () => {
    const plugins = read("components/desktop/PluginCenterView.tsx");
    const projectSection = plugins.slice(
      plugins.indexOf('"project-work"'),
      plugins.indexOf('"publisher-work"'),
    );
    expect(projectSection).not.toContain("view:");
    expect(plugins).toContain("desktopView: meta.view || null");
    expect(projectSection).not.toContain("gworkspace");
  });
});
