import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();

describe("V2000 agent workspace contract", () => {
  it("exposes the full agent catalog from the user sidebar", () => {
    const source = fs.readFileSync(path.join(root, "components/Sidebar.tsx"), "utf8");
    expect(source).toContain('v: "agents"');
    expect(source).toContain('label: "智能体"');
  });

  it("binds team roles to catalog agent ids", () => {
    const source = fs.readFileSync(path.join(root, "components/TeamPanel.tsx"), "utf8");
    expect(source).toContain("teamAgents()");
    expect(source).toContain("agent_id: selected?.id");
    expect(source).toContain("完整角色库");
  });
});
