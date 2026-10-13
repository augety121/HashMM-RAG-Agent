import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  flattenRelativeAttachmentName,
  normalizeTodoManifestVersion,
  shouldAcceptTodoManifest,
} from "../lib/chatDelivery";

const root = path.resolve(__dirname, "..");

describe("V700 Chat delivery integrity", () => {
  it("preserves folder identity without allowing path separators", () => {
    expect(flattenRelativeAttachmentName(
      "research/primary/source.md",
      "source.md",
    )).toBe("research__primary__source.md");
    expect(flattenRelativeAttachmentName(
      "../bad:<name>.txt",
      "fallback.txt",
    )).toBe("bad__name_.txt");
  });

  it("rejects replayed or unrelated todo manifests", () => {
    const current = { id: "turn-1:todo", revision: 4 };
    expect(shouldAcceptTodoManifest(current, { id: "turn-1:todo", revision: 4 })).toBe(true);
    expect(shouldAcceptTodoManifest(current, { id: "turn-1:todo", revision: 5 })).toBe(true);
    expect(shouldAcceptTodoManifest(current, { id: "turn-1:todo", revision: 3 })).toBe(false);
    expect(shouldAcceptTodoManifest(current, { id: "turn-2:todo", revision: 8 })).toBe(false);
    expect(shouldAcceptTodoManifest(current, null)).toBe(false);
    expect(normalizeTodoManifestVersion({ manifest_id: " m ", revision: -2 })).toEqual({
      id: "m",
      revision: 0,
    });
  });

  it("persists the conversation and attachments before clearing the composer", () => {
    const source = fs.readFileSync(
      path.join(root, "components", "ChatArea.tsx"),
      "utf8",
    );
    const begin = source.indexOf("const currentFiles = regenerate");
    const end = source.indexOf("// 截屏类附件", begin);
    const delivery = source.slice(begin, end);
    expect(delivery.indexOf("await createConversation")).toBeGreaterThan(-1);
    expect(delivery.indexOf("await createConversation")).toBeLessThan(delivery.indexOf("addSession({"));
    expect(delivery.indexOf("await uploadConversationFile")).toBeLessThan(delivery.indexOf("addMsg(id"));
    expect(delivery.indexOf("addMsg(id")).toBeLessThan(delivery.indexOf("setText(\"\")"));
    expect(delivery).not.toContain("`/api/files/${encodeURIComponent(n)}`");
  });
});
