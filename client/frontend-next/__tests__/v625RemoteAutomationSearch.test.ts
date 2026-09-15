import fs from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";

const frontend = process.cwd();
const read = (file: string) => fs.readFileSync(path.join(frontend, file), "utf8");

describe("V627 user-facing relay, automation and search", () => {
  test("automatic tasks are a first-class sidebar surface backed by real routine APIs", () => {
    const sidebar = read("components/Sidebar.tsx");
    const view = read("components/desktop/ScheduledView.tsx");
    expect(sidebar).toContain('label: "自动任务"');
    expect(view).toContain("listUserRoutines");
    expect(view).toContain("createUserRoutine");
    expect(view).toContain("runUserRoutine");
    expect(view).toContain("TEMPLATES");
    expect(view).toContain("结果回到创建它的对话");
    expect(view).toContain("登录后使用自动任务");
    expect(view).toContain("自动任务暂时不可用");
    expect(view).toContain("last_result");
    expect(view).toContain("next_run");
  });

  test("device relay is a device workspace instead of three navigation cards", () => {
    const view = read("components/desktop/RemoteView.tsx");
    expect(view).toContain("我的设备");
    expect(view).toContain("App 接力");
    expect(view).toContain("进入桌面");
    expect(view).toContain("getRunners");
    expect(view).toContain("openDirectViewer");
    expect(view).toContain("runnerState");
    expect(view).toContain("today_done");
    expect(view).not.toContain('["handoff", Smartphone');
  });

  test("plugin and administration share the account search integration", () => {
    const view = read("components/desktop/PluginCenterView.tsx");
    const settings = read("components/settings/SearchIntegrationSettings.tsx");
    const admin = read("components/admin/SettingsTab.tsx");
    const api = read("lib/api.ts");
    expect(api).toContain("/test");
    expect(view).toContain("SearchIntegrationSettings");
    expect(settings).toContain("testSearchIntegration");
    expect(settings).toContain("测试真实连接");
    expect(settings).toContain("账号之间配置隔离");
    expect(settings).toContain("当前服务器还没有联网搜索配置接口");
    expect(admin).toContain("SearchIntegrationSettings");
    expect(admin).toContain("平台检索兜底");
    expect(admin).toContain("与账号级搜索分开管理");
  });
});
