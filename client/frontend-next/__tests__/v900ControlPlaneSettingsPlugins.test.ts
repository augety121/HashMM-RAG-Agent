import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(__dirname, "..");
const read = (...parts: string[]) => fs.readFileSync(path.join(root, ...parts), "utf8");

describe("control-plane and settings truthfulness", () => {
  it("commits rotated credentials after password change", () => {
    const api = read("lib", "api.ts");
    const settings = read("components", "SettingsModal.tsx");
    expect(api).toContain("changeMyPassword");
    expect(api).toContain("setTokens(result.token, result.refresh_token)");
    expect(settings).toContain("密码至少 8 位，并同时包含字母和数字");
    expect(settings).not.toContain('await fetch("/api/auth/password"');
  });

  it("updates the current profile through the self-service API", () => {
    const settings = read("components", "SettingsModal.tsx");
    const profile = read("components", "ProfileModal.tsx");
    expect(settings).toContain("updateMyAccountProfile");
    expect(settings).not.toContain("fetch(`/api/admin/users/${user?.id}`");
    expect(profile).toContain("updateMyAccountProfile(name)");
  });

  it("uses revisioned account settings and a server receipt", () => {
    const settings = read("components", "SettingsModal.tsx");
    expect(settings).toContain("listMyAccountSettings");
    expect(settings).toContain("updateMyAccountSettings");
    expect(settings).toContain("变更回执");
    expect(settings).toContain("设置已在其他设备修改");
  });

  it("adds a truthful overview instead of the disconnected legacy dashboard", () => {
    const admin = read("components", "AdminPanel.tsx");
    const overview = read("components", "admin", "OverviewTab.tsx");
    expect(admin).toContain('tab: "overview"');
    expect(overview).toContain("读取失败不会填充为 0");
    expect(overview).toContain('value == null ? "—"');
    expect(overview).toContain("数据源已验证");
  });
});

describe("plugin supply-chain controls", () => {
  it("uploads a checked ZIP without auto-trusting or executing it", () => {
    const api = read("lib", "api.ts");
    const center = read("components", "desktop", "PluginCenterView.tsx");
    expect(api).toContain('crypto.subtle.digest("SHA-256"');
    expect(api).toContain('"X-Plugin-Sha256": sha256');
    expect(center).toContain("当前未执行任何插件代码");
    expect(center).toContain("Python 插件仍需审核摘要后加载");
    expect(center).toContain("声明式集成只需核对权限与配置，不进入代码信任流程");
    expect(center).toContain("导入后不会自动信任或执行");
  });

  it("shows permissions, digest and tool annotations before trust", () => {
    const center = read("components", "desktop", "PluginCenterView.tsx");
    expect(center).toContain("安全诊断");
    expect(center).toContain("diagnostics.permissions.filesystem");
    expect(center).toContain("diagnostics.sha256");
    expect(center).toContain("tool.annotations");
  });
});
