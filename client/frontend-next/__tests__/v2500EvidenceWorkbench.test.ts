import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

describe("V2500 Evidence Workbench product wiring", () => {
  it("exposes Provider Fabric as a real user workspace", () => {
    const panel = fs.readFileSync(path.join(root, "components", "ProviderFabricPanel.tsx"), "utf8");
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    const admin = fs.readFileSync(path.join(root, "components", "AdminPanel.tsx"), "utf8");
    expect(panel).toContain("Sub2API");
    expect(panel).toContain("用于 Chat");
    expect(api).toContain("/api/provider-fabric");
    expect(admin).toContain("ProviderFabricPanel");
  });

  it("keeps Chat tools capability-aware and provides Focus mode", () => {
    const chat = fs.readFileSync(path.join(root, "components", "ChatArea.tsx"), "utf8");
    expect(chat).toContain("runtimeCapabilities()");
    expect(chat).toContain('runtimeReady.has("multi_agent")');
    expect(chat).toContain('runtimeReady.has("canvas")');
    expect(chat).toContain("hmm_chat_focus_mode");
    expect(chat).toContain("证据保留在右侧检查器");
  });
});
