import { beforeEach, describe, expect, it } from "vitest";
import {
  WORK_OUTBOX_SCHEMA,
  drainWorkOutbox,
  enqueueWorkCommand,
  listWorkOutbox,
  removeWorkCommand,
} from "@/lib/workOutbox";

describe("work command outbox", () => {
  beforeEach(() => localStorage.clear());

  it("deduplicates by owner and command id without storing task content", () => {
    enqueueWorkCommand({
      commandId: "cmd-1", runId: "run-1", ownerId: "alice",
      action: "cancel", expectedRevision: 3,
    });
    enqueueWorkCommand({
      commandId: "cmd-1", runId: "run-1", ownerId: "alice",
      action: "cancel", expectedRevision: 4,
    });
    const items = listWorkOutbox("alice");
    expect(items).toHaveLength(1);
    expect(items[0].expectedRevision).toBe(4);
    expect(items[0].schema).toBe(WORK_OUTBOX_SCHEMA);
    expect(JSON.stringify(items)).not.toContain("prompt");
  });

  it("drains acknowledged commands in FIFO order and stops on network loss", async () => {
    enqueueWorkCommand({
      commandId: "cmd-a", runId: "run-a", ownerId: "alice",
      action: "pause", expectedRevision: 1,
    });
    enqueueWorkCommand({
      commandId: "cmd-b", runId: "run-b", ownerId: "alice",
      action: "cancel", expectedRevision: 2,
    });
    const seen: string[] = [];
    const result = await drainWorkOutbox("alice", async (entry) => {
      seen.push(entry.commandId);
      if (entry.commandId === "cmd-b") throw new Error("Failed to fetch");
      return { ok: true };
    });
    expect(seen).toEqual(["cmd-a", "cmd-b"]);
    expect(result.sent).toBe(1);
    expect(result.pending).toBe(1);
    expect(result.stoppedOnNetworkError).toBe(true);
    removeWorkCommand("alice", "cmd-b");
    expect(listWorkOutbox("alice")).toHaveLength(0);
  });
});

