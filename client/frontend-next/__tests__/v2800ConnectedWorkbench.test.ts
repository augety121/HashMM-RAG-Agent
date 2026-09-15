import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

describe("V2800 connected workbench", () => {
  it("mounts the owner-scoped Provider Fabric in ordinary user settings", () => {
    const settings = fs.readFileSync(path.join(root, "components", "SettingsModal.tsx"), "utf8");
    expect(settings).toContain('label: "模型与 API"');
    expect(settings).toContain("<ProviderFabricPanel />");
    expect(settings).toContain("兼容模式：直接添加单一模型");
  });

  it("uses a real attachment queue instead of a fake fixed progress bar", () => {
    const chat = fs.readFileSync(path.join(root, "components", "ChatArea.tsx"), "utf8");
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    expect(chat).toContain("readDroppedEntry");
    expect(chat).toContain("uploadAttachmentBatch");
    expect(chat).toContain("attachmentDrafts[draftKey]");
    expect(chat).toContain("uploadControllersRef.current.get(f.id)?.abort()");
    expect(chat).toContain("controller.signal");
    expect(chat).toContain('state: "uploading"');
    expect(chat).not.toContain('width: "60%"');
    expect(api).toContain("xhr.upload.onprogress");
  });

  it("does not probe authenticated runtime capabilities before token restoration", () => {
    const chat = fs.readFileSync(path.join(root, "components", "ChatArea.tsx"), "utf8");
    expect(chat).toContain("if (!token)");
    expect(chat).toContain("runtimeCapabilities().then");
  });
});
