import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V387 connected work receipts", () => {
  const root = path.resolve(__dirname, "..");

  it("shows bounded remote measurements and can hand them to Chat", () => {
    const view = fs.readFileSync(path.join(root, "components", "desktop", "RemoteView.tsx"), "utf8");
    expect(view).toContain("hashmm.remote-quality.v1");
    expect(view).toContain("packetLossPct");
    expect(view).toContain("availableOutgoingBitrateKbps");
    expect(view).toContain("framesPerSecond");
    expect(view).toContain("insertContextIntoChat");
    expect(view).not.toContain("candidateAddress");
    expect(view).not.toContain("localDescription");
  });

  it("confirms the exact Office revision committed by the backend", () => {
    const panel = fs.readFileSync(path.join(root, "components", "ArtifactPanel.tsx"), "utf8");
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    const desktop = fs.readFileSync(path.join(root, "lib", "desktop.ts"), "utf8");
    expect(api).toContain("sha256: string");
    expect(panel).toContain("uploaded.sha256 !== local.sha256");
    expect(panel).toContain("officeHandoffAcknowledge(officeHandoff.id, local.sha256)");
    expect(panel).toContain("Chat 下轮将读取新文件");
    expect(desktop).toContain("conflict?: boolean");
  });

  it("opens conversation file metadata from cache and revalidates by ETag", () => {
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    const panel = fs.readFileSync(path.join(root, "components", "ConvFilePanel.tsx"), "utf8");
    expect(api).toContain("hashmm-conversation-file-lists-v1");
    expect(api).toContain('"If-None-Match": cached.etag');
    expect(api).toContain("response.status === 304");
    expect(api).toContain("onConvFileListUpdated");
    expect(panel).toContain("onConvFileListUpdated(convId");
  });
});
