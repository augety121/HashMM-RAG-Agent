import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const frontend = process.cwd();
const read = (file: string) => fs.readFileSync(path.join(frontend, file), "utf8");

describe("V610 user-first work surfaces", () => {
  test("Canvas starts with an outcome instead of an implementation mode", () => {
    const view = read("components/desktop/CanvasHomeView.tsx");
    const css = read("app/globals.css");
    for (const label of ["自由创作", "整理一项工作", "比较并做决定", "呈现数据", "共同完成"]) {
      expect(view).toContain(label);
    }
    expect(view).toContain("创建并打开");
    expect(view).toContain("saveConvFile");
    expect(view).toContain("openArtifact");
    expect(css).toContain(".canvas-purpose-card[data-active=\"true\"]");
  });

  test("Collaboration is a confirm-before-run user flow backed by real team APIs", () => {
    const view = read("components/desktop/AgentsStudioView.tsx");
    for (const label of ["说明工作", "确认分工", "查看结果", "希望拿到什么结果", "什么样才算完成"]) {
      expect(view).toContain(label);
    }
    expect(view).toContain("teamPreviewV2");
    expect(view).toContain("teamStart");
    expect(view).toContain("teamStatus");
    expect(view).toContain("teamStop");
    expect(view).toContain("teamRetry");
    expect(view).toContain("使用的资料依据");
    expect(view).toContain("查看执行记录");
    expect(view).not.toContain("共享 RAG 证据");
    expect(view).not.toContain("执行泳道");
  });

  test("Today, projects and library expose user decisions rather than storage internals", () => {
    const view = read("components/desktop/WorkspaceShellView.tsx");
    for (const label of ["需要你决定", "正在替你处理", "最近完成", "先说清要做什么", "约定怎样算完成", "接下来想做什么"]) {
      expect(view).toContain(label);
    }
    expect(view).toContain("createWorkProject");
    expect(view).toContain("listKnowledgeDocuments");
    expect(view).toContain("帮我比较");
    expect(view).not.toContain("个可检索片段");
  });

  test("Plugins and device relay keep admin and networking details below the user path", () => {
    const plugins = read("components/desktop/PluginCenterView.tsx");
    const remote = read("components/desktop/RemoteView.tsx");
    expect(plugins).toContain("能力与插件");
    expect(plugins).toContain("技能");
    expect(plugins).toContain("runtimeCapabilities()");
    expect(remote).toContain("接回 App 上的工作");
    expect(remote).toContain("连接另一台电脑");
    expect(remote).toContain("安全控制");
    expect(remote).toContain("远程办公");
    expect(remote).toContain("流畅画面");
    expect(remote).toContain("连接帮助");
    expect(remote).toContain("isAdmin &&");
  });
});
