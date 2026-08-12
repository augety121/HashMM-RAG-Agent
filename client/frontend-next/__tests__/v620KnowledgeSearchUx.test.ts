import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const frontend = process.cwd();
const read = (file: string) => fs.readFileSync(path.join(frontend, file), "utf8");

describe("V620 knowledge, search and user-facing work surfaces", () => {
  test("the library performs an explicit OKF preview and confirmation flow", () => {
    const view = read("components/desktop/WorkspaceShellView.tsx");
    const api = read("lib/api.ts");
    expect(api).toContain("/api/okf/preview");
    expect(api).toContain("/api/okf/apply");
    expect(view).toContain("previewOkfPack");
    expect(view).toContain("applyOkfPack");
    expect(view).toContain("确认导入");
    expect(view).toContain("可信度");
    expect(view).toContain("导出知识包");
  });

  test("search configuration is an account-scoped Chat integration, not a debug form", () => {
    const view = read("components/desktop/PluginCenterView.tsx");
    const settings = read("components/settings/SearchIntegrationSettings.tsx");
    const api = read("lib/api.ts");
    expect(api).toContain("/api/integrations/search/");
    expect(view).toContain("联网服务");
    expect(settings).toContain("凭据只在服务端加密保存");
    expect(settings).toContain("saveSearchIntegration");
    expect(settings).toContain("在 Chat 中试用");
    expect(view).toContain('pendingRunMode: "deep"');
  });

  test("device relay keeps real clients behind a user-facing device workspace", () => {
    const view = read("components/desktop/RemoteView.tsx");
    for (const label of ["App 接力", "我的设备", "这台电脑", "安全控制", "远程办公", "流畅画面"]) {
      expect(view).toContain(label);
    }
    expect(view).toContain("remoteSection");
    expect(view).toContain("openDirectViewer");
    expect(view).toContain("launchRdp");
    expect(view).toContain("launchMoonlight");
    expect(view).toContain("networkBridgeStatus");
  });

  test("Canvas and collaboration use persistent objects and return work to Chat", () => {
    const canvas = read("components/desktop/CanvasHomeView.tsx");
    const team = read("components/desktop/AgentsStudioView.tsx");
    expect(canvas).toContain("startCanvasChat");
    expect(canvas).toContain("saveConvFile");
    expect(canvas).toContain("openArtifact");
    expect(team).toContain("teamPreviewV2");
    expect(team).toContain("teamStart");
    expect(team).toContain("最近协作");
    expect(team).toContain("查看执行记录");
  });

  test("new Chat closes user workspaces instead of leaving stale content visible", () => {
    const store = read("lib/store.ts");
    expect(store).toContain("newChat: () => {");
    expect(store).toContain("desktopView: null");
    expect(store).toContain("adminOpen: false");
    expect(store).toContain("setOpen: false");
    expect(store).toContain('pendingRunMode: ""');
  });
});
