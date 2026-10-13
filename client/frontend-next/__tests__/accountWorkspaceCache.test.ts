import { beforeEach, describe, expect, it } from "vitest";
import {
  clearAccountWorkspaceCache,
  readAccountProjects,
  readAccountSessions,
  readActiveProject,
  writeAccountProjects,
  writeAccountSessions,
  writeActiveProject,
} from "@/lib/accountWorkspaceCache";
import type { Session, User } from "@/lib/types";
import type { WorkProject } from "@/lib/api";

const alice: User = { id: "user-alice", username: "alice", display_name: "Alice", role: "user" };
const bob: User = { id: "user-bob", username: "bob", display_name: "Bob", role: "user" };

const session = (id: string, projectId = ""): Session => ({
  id,
  title: `对话 ${id}`,
  messages: [],
  created: 100,
  updated_at: 200,
  project_id: projectId || undefined,
});

const project = (id: string): WorkProject => ({
  id,
  user_id: alice.id,
  name: `项目 ${id}`,
  description: "",
  goal: "",
  deliverable: "",
  success_criteria: [],
  permission_mode: "ask",
  status: "active",
  archived: false,
  revision: 1,
  conv_count: 1,
  created_at: 100,
  updated_at: 200,
});

describe("account workspace cache", () => {
  beforeEach(() => localStorage.clear());

  it("keeps project, conversation and active project isolated by owner", () => {
    writeAccountSessions(alice, [session("alice-chat", "project-a")]);
    writeAccountProjects(alice, [project("project-a")]);
    writeActiveProject(alice, "project-a");

    writeAccountSessions(bob, [session("bob-chat")]);
    writeActiveProject(bob, "");

    expect(readAccountSessions(alice).map(item => item.id)).toEqual(["alice-chat"]);
    expect(readAccountSessions(bob).map(item => item.id)).toEqual(["bob-chat"]);
    expect(readAccountProjects(alice).map(item => item.id)).toEqual(["project-a"]);
    expect(readAccountProjects(bob)).toEqual([]);
    expect(readActiveProject(alice)).toBe("project-a");
    expect(readActiveProject(bob)).toBe("");
  });

  it("migrates the legacy global session cache once, never into two accounts", () => {
    localStorage.setItem("hmm_s", JSON.stringify([session("legacy-chat")]));

    expect(readAccountSessions(alice).map(item => item.id)).toEqual(["legacy-chat"]);
    expect(localStorage.getItem("hmm_s")).toBeNull();
    expect(readAccountSessions(bob)).toEqual([]);
  });

  it("clears only the requested owner's local workspace", () => {
    writeAccountSessions(alice, [session("alice-chat")]);
    writeAccountSessions(bob, [session("bob-chat")]);

    clearAccountWorkspaceCache(alice);

    expect(readAccountSessions(alice)).toEqual([]);
    expect(readAccountSessions(bob).map(item => item.id)).toEqual(["bob-chat"]);
  });

  it("keeps the complete recent index beyond the old 120-row cap", () => {
    const rows = Array.from({ length: 180 }, (_, index) => ({
      ...session(`chat-${index}`),
      updated_at: 1000 - index,
      messages: [{ id: `m-${index}`, role: "user" as const, content: "cached body" }],
    }));
    writeAccountSessions(alice, rows);

    const cached = readAccountSessions(alice);
    expect(cached).toHaveLength(180);
    expect(cached[0].messages).toHaveLength(1);
    expect(cached[79].messages).toHaveLength(1);
    expect(cached[80].messages).toEqual([]);
    expect(cached[179].id).toBe("chat-179");
  });
});
