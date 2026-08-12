import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

describe("V702 account-scoped project chat continuity", () => {
  it("starts a fresh project chat without reusing the selected conversation", () => {
    const sidebar = fs.readFileSync(
      path.join(root, "components", "Sidebar.tsx"),
      "utf8",
    );
    const begin = sidebar.indexOf("function startProjectChat");
    const end = sidebar.indexOf("\n  function ", begin + 10);
    const implementation = sidebar.slice(begin, end);
    expect(implementation).toContain("newChat()");
    expect(implementation).toContain("writeActiveProject");
    expect(implementation).toContain("setActiveProjectId");
    expect(implementation.indexOf("writeActiveProject")).toBeLessThan(
      implementation.indexOf("newChat()"),
    );
  });

  it("creates the conversation inside the selected project before uploading", () => {
    const chat = fs.readFileSync(
      path.join(root, "components", "ChatArea.tsx"),
      "utf8",
    );
    const begin = chat.indexOf("const currentFiles = regenerate");
    const end = chat.indexOf("// 截屏类附件", begin);
    const send = chat.slice(begin, end);
    expect(send).toContain("readActiveProject");
    expect(send).toContain("await createConversation");
    expect(send).toContain("selectedProjectId");
    expect(send.indexOf("await createConversation")).toBeLessThan(
      send.indexOf("await uploadAttachmentBatch"),
    );
    expect(send.indexOf("await uploadAttachmentBatch")).toBeLessThan(
      send.indexOf("addMsg(id"),
    );
  });

  it("marks locally interrupted partial output for honest resume semantics", () => {
    const chat = fs.readFileSync(
      path.join(root, "components", "ChatArea.tsx"),
      "utf8",
    );
    const begin = chat.indexOf("onCancelled: () =>");
    const end = chat.indexOf("\n      },", begin) + 8;
    const callback = chat.slice(begin, end);
    expect(callback).toContain('stop_reason: "interrupted"');
    expect(callback).toContain("reconcileConversationAfterStop");
  });

  it("asks the server to stop the exact active turn before aborting the local stream", () => {
    const chat = fs.readFileSync(
      path.join(root, "components", "ChatArea.tsx"),
      "utf8",
    );
    const begin = chat.indexOf("async function interruptCurrentTurn");
    const end = chat.indexOf("\n  function ", begin + 10);
    const implementation = chat.slice(begin, end);
    expect(implementation).toContain("getActiveTurn");
    expect(implementation).toContain("await interruptActiveTurn");
    expect(implementation.indexOf("await interruptActiveTurn")).toBeLessThan(
      implementation.indexOf("stopRef.current = true"),
    );
  });

  it("reconciles an interrupted task from the durable server message and public process manifest", () => {
    const chat = fs.readFileSync(
      path.join(root, "components", "ChatArea.tsx"),
      "utf8",
    );
    const begin = chat.indexOf("async function reconcileConversationAfterStop");
    const end = chat.indexOf("\n  function ", begin + 10);
    const implementation = chat.slice(begin, end);
    expect(implementation).toContain("loadConversation");
    expect(implementation).toContain("run_manifest?.process");
    expect(implementation).toContain("timeline");
    expect(implementation).toContain("todo");
    expect(implementation).toContain("persistSessions");
  });
});
