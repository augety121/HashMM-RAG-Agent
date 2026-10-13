import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { artifactTypeOf, isPanelPreviewable } from "../lib/artifact";

describe("V361 desktop reliability and document workspace", () => {
  const root = path.resolve(__dirname, "..");

  it("opens PDF and image files in the unified right panel", () => {
    expect(artifactTypeOf("report.pdf")).toBe("pdf");
    expect(artifactTypeOf("screen.webp")).toBe("image");
    expect(isPanelPreviewable("report.pdf")).toBe(true);
    expect(isPanelPreviewable("archive.zip")).toBe(false);
  });

  it("keeps composer drafts scoped by account and conversation", () => {
    const source = fs.readFileSync(path.join(root, "components", "ChatArea.tsx"), "utf8");
    expect(source).toContain("hmm_composer_drafts:${userId");
    expect(source).toContain("const draftKey = sid || NEW_CHAT_DRAFT");
    expect(source).toContain("const text = composerDrafts[draftKey] || \"\"");
    expect(source).toContain("delete updated[draftKey]");
  });

  it("does not dereference optional fields in an older run manifest", () => {
    const source = fs.readFileSync(path.join(root, "components", "RightContextPanel.tsx"), "utf8");
    expect(source).toContain("const runVerification = runManifest?.verification");
    expect(source).not.toContain("runManifest.verification.status");
    expect(source).not.toContain("runManifest.termination.reason");
    expect(source).toContain("暂无完整校验");
  });

  it("uses persistent conditional preview caching and an explicit PPT failure state", () => {
    const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
    const panel = fs.readFileSync(path.join(root, "components", "ArtifactPanel.tsx"), "utf8");
    expect(api).toContain("If-None-Match");
    expect(api).toContain("hashmm-file-previews-v1");
    expect(panel).toContain("演示文稿暂时无法预览");
    expect(panel).toContain("onPreviewFileUpdated");
  });

  it("presents exactly seven user-facing help paths and ten setting destinations", () => {
    const help = fs.readFileSync(path.join(root, "components", "HelpModals.tsx"), "utf8");
    const settings = fs.readFileSync(path.join(root, "components", "SettingsModal.tsx"), "utf8");
    for (const id of ["chat", "long-task", "files", "knowledge", "browser", "agents", "privacy"]) {
      expect(help).toContain(`id: "${id}"`);
    }
    expect((settings.match(/\{ id: "(general|notification|personalize|mymodels|data|storage|security|account|shortcuts|about)"/g) || [])).toHaveLength(10);
    expect(settings).toContain("TAB_SCOPE");
  });
});
