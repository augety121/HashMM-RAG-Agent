import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "..");

describe("V703 Codex-style public task chain", () => {
  const panel = fs.readFileSync(
    path.join(root, "components", "RightContextPanel.tsx"),
    "utf8",
  );

  it("opens the durable task and sub-agent view by default", () => {
    expect(panel).toContain('useState<Tab>("agents")');
    expect(panel).toContain('label: "子智能体"');
    expect(panel).toContain("公开任务链");
    expect(panel).toContain("服务端持久化");
  });

  it("loads the work feed for both task-chain and detailed runtime tabs", () => {
    expect(panel).toContain('(tab !== "run" && tab !== "agents")');
    expect(panel).toContain("activeWorkItems");
    expect(panel).toContain("completedWorkItems");
    expect(panel).toContain("workRuns.slice(0, 8)");
  });

  it("shows audit records without claiming to reveal hidden reasoning", () => {
    expect(panel).toContain("不展示模型隐藏推理");
    expect(panel).toContain("计划、步骤、证据、审批和结果");
  });
});
