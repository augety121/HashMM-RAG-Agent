import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("V390 evidence-native work surface", () => {
  const frontend = path.resolve(__dirname, "..");
  const repo = path.resolve(frontend, "..");

  it("types context capsules, causal generations and proof-carrying actions", () => {
    const types = fs.readFileSync(path.join(frontend, "lib", "types.ts"), "utf8");
    expect(types).toContain('schema: "hashmm.execution-receipt.v1"');
    expect(types).toContain('schema: "hashmm.context-capsule.v1"');
    expect(types).toContain('schema: "hashmm.causal-work-graph.v1"');
    expect(types).toContain("raw_arguments_included: false");
    expect(types).toContain("source_bodies_included: false");
  });

  it("shows the same receipts and invalidation frontier in the right work panel", () => {
    const panel = fs.readFileSync(
      path.join(frontend, "components", "RightContextPanel.tsx"), "utf8",
    );
    expect(panel).toContain("因果工作图");
    expect(panel).toContain("执行收据");
    expect(panel).toContain("只重验受变化来源影响");
    expect(panel).toContain("不保存原始参数、凭证或工具正文");
  });

  it("projects the evidence fabric through the durable cross-device manifest", () => {
    const runtime = fs.readFileSync(
      path.join(repo, "hashmm", "agent", "work_runtime.py"), "utf8",
    );
    expect(runtime).toContain('"causal_work_graph"');
    expect(runtime).toContain('"execution_receipts"');
    expect(runtime).toContain('"context_capsule"');
  });

  it("keeps long tasks and team workers on the same proof surface", () => {
    const workspace = fs.readFileSync(
      path.join(frontend, "components", "desktop", "GlobalWorkspaceView.tsx"), "utf8",
    );
    const worker = fs.readFileSync(
      path.join(repo, "hashmm", "agent", "worker.py"), "utf8",
    );
    expect(workspace).toContain("因果工作图");
    expect(workspace).toContain("无效回执");
    expect(worker).toContain("build_execution_receipt");
    expect(worker).toContain('"execution_receipts"');
  });
});
