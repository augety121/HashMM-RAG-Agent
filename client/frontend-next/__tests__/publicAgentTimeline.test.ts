import { describe, expect, it } from "vitest";
import { toPublicAgentTimeline } from "@/lib/publicAgentTimeline";

describe("public agent timeline", () => {
  it("removes transport events and private reasoning from the Chat timeline", () => {
    const publicEvents = toPublicAgentTimeline([
      { node: "turn_admitted", detail: "turn admitted", status: "done" },
      { node: "context_assembled", detail: "context assembled", status: "done" },
      { node: "iteration_started", detail: "iteration started", status: "running" },
      { node: "think", detail: "private provider reasoning", status: "running" },
      { node: "turn_finished", detail: "turn finished", status: "done" },
    ]);

    expect(publicEvents).toHaveLength(1);
    expect(publicEvents[0].node).toBe("analysis");
    expect(publicEvents[0].detail).toBe("正在推进任务并检查下一步");
    expect(JSON.stringify(publicEvents)).not.toContain("private provider reasoning");
    expect(JSON.stringify(publicEvents)).not.toContain("turn admitted");
  });

  it("uses user-facing labels for source discovery and verification", () => {
    const publicEvents = toPublicAgentTimeline([
      { node: "tool", tool: "web_search", detail: "raw query and five urls", status: "done" },
      { node: "tool", tool: "fetch_url", detail: "raw html response", status: "done" },
      { node: "tool", tool: "create_file", detail: "C:\\private\\brief.md", status: "done" },
    ]);

    expect(publicEvents.map(event => event.tool)).toEqual(["联网搜索", "打开来源", "生成文件"]);
    expect(JSON.stringify(publicEvents)).not.toContain("raw query");
    expect(JSON.stringify(publicEvents)).not.toContain("C:\\private");
  });

  it("folds repeated adjacent operations without hiding their terminal state", () => {
    const publicEvents = toPublicAgentTimeline([
      { node: "tool", tool: "web_search", detail: "q1", status: "running", id: "one" },
      { node: "tool", tool: "web_search", detail: "q1 result", status: "done", elapsed_ms: 3200, id: "two" },
    ]);

    expect(publicEvents).toHaveLength(1);
    expect(publicEvents[0]).toMatchObject({
      tool: "联网搜索",
      status: "done",
      elapsed_ms: 3200,
      id: "one",
    });
  });
});
