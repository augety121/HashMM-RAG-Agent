import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

describe("V810 project-resource-OCR continuity", () => {
  const api = fs.readFileSync(path.join(root, "lib", "api.ts"), "utf8");
  const chat = fs.readFileSync(path.join(root, "components", "ChatArea.tsx"), "utf8");
  const message = fs.readFileSync(path.join(root, "components", "MsgBubble.tsx"), "utf8");
  const inspector = fs.readFileSync(path.join(root, "components", "RightContextPanel.tsx"), "utf8");

  it("uses authenticated server persistence for conversation instructions", () => {
    expect(api).toContain("getConversationPrompt");
    expect(api).toContain("saveConversationPrompt");
    expect(chat).toContain("saveConversationPrompt(sid, convPrompt)");
    expect(chat).not.toContain('headers: {"Content-Type":"application/json"}');
  });

  it("projects durable OCR progress and controls without exposing server paths", () => {
    expect(api).toContain('schema: "hashmm.ocr-job.v2"');
    expect(api).not.toContain("result_path?: string");
    expect(message).toContain("cancelOcrJob");
    expect(message).toContain("OCR 部分可读");
    expect(inspector).toContain("附件解析与 OCR");
    expect(inspector).toContain("job.processed_pages");
  });

  it("exposes server-authoritative project resource membership", () => {
    expect(api).toContain("listProjectResources");
    expect(api).toContain("addProjectResource");
    expect(api).toContain("removeProjectResource");
  });
});
