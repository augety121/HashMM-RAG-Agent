import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

describe("V590 plugin, Skills, composer and Canvas surfaces are real", () => {
  const frontend = process.cwd();

  test("plugin client uses the mounted API and the page is backed by runtime truth", () => {
    const api = fs.readFileSync(path.join(frontend, "lib", "api.ts"), "utf8");
    const view = fs.readFileSync(path.join(frontend, "components", "desktop", "PluginCenterView.tsx"), "utf8");
    expect(api).toContain('return _fetch("/api/plugins"');
    expect(api).not.toContain('return _fetch("/api/system/plugins"');
    expect(view).toContain("runtimeCapabilities()");
    expect(view).toContain("isCapabilityVisible");
    expect(view).toContain("无需安装");
    expect(view).toContain("只展示已经审核并能在 Chat 中实际使用的插件");
  });

  test("personal Skills are a first-class tab next to plugins", () => {
    const view = fs.readFileSync(path.join(frontend, "components", "desktop", "PluginCenterView.tsx"), "utf8");
    expect(view).toContain('<UserSkillsSettings />');
    expect(view).toContain("我的工作方法");
    expect(view).toContain('setTab("skills")');
  });

  test("composer restores direct work controls and keeps document scope visible", () => {
    const chat = fs.readFileSync(path.join(frontend, "components", "ChatArea.tsx"), "utf8");
    const css = fs.readFileSync(path.join(frontend, "app", "globals.css"), "utf8");
    for (const label of ["浏览器", "深度检索", "多智能体", "画布", "电脑操作"]) {
      expect(chat).toContain(label);
    }
    expect(chat).toContain("<DocFilterChips");
    expect(chat).not.toContain("composer-work-options");
    expect(css).toContain("@container chat-area (max-width: 620px)");
    expect(css).toContain(".composer-tools .composer-tool-label { display: inline; }");
    expect(css).toContain(".composer-tools .composer-tool-label { display: none; }");
  });

  test("Canvas style controls are grouped without removing Agent handoffs", () => {
    const artifact = fs.readFileSync(path.join(frontend, "components", "ArtifactPanel.tsx"), "utf8");
    expect(artifact).toContain("canvas-style-popover");
    expect(artifact).toContain("<Palette");
    expect(artifact).toContain("让 Agent 修改");
    expect(artifact).toContain("浏览器事实核验");
    expect(artifact).toContain("多 Agent 并行评审");
  });
});
