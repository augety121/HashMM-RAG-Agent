import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V386 unified Agent harness panel", () => {
  const root = path.resolve(__dirname, "..");

  it("declares the bounded harness on the persisted run manifest", () => {
    const types = fs.readFileSync(path.join(root, "lib", "types.ts"), "utf8");
    expect(types).toContain('schema: "hashmm.agent-harness.v1"');
    expect(types).toContain("harness?: AgentHarness");
    expect(types).toContain("args_hash?: string");
  });

  it("renders runtime facts without exposing raw tool arguments", () => {
    const panel = fs.readFileSync(path.join(root, "components", "RightContextPanel.tsx"), "utf8");
    expect(panel).toContain("Agent 运行内核");
    expect(panel).toContain("真实可执行能力");
    expect(panel).toContain("已在模型调用前移除");
    expect(panel).toContain("不保存原始工具参数、凭据或工具正文");
    expect(panel).not.toContain("runHarness.trajectory.events[0].args");
  });
});
