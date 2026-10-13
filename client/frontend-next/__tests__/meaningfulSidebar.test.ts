import { describe, expect, it } from "vitest";
import { isMeaningfulSession, isSidebarVisibleSession } from "@/lib/store";

describe("meaningful conversation projection", () => {
  it("hides a reserved shell until a durable item exists", () => {
    expect(isMeaningfulSession({ id: "shell", title: "Canvas", messages: [], created: 1 })).toBe(false);
    expect(isMeaningfulSession({
      id: "run", title: "Task", messages: [{ role: "assistant", content: "", ts: 1, status: "waiting_approval" }], created: 1,
    })).toBe(true);
    expect(isMeaningfulSession({
      id: "msg", title: "Task", messages: [{ role: "user", content: "start", ts: 1 }], created: 1,
    })).toBe(true);
  });

  it("keeps server-confirmed history visible when message bodies are not cached", () => {
    expect(isSidebarVisibleSession({
      id: "old-server-chat", title: "Old", messages: [], created: 1,
      has_durable_content: true, visibility_source: "server",
    })).toBe(true);
    expect(isSidebarVisibleSession({
      id: "server-shell", title: "Shell", messages: [{ role: "user", content: "local stale", ts: 1 }], created: 1,
      has_durable_content: false, visibility_source: "server",
    })).toBe(false);
  });
});
