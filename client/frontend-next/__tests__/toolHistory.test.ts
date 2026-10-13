import { describe, expect, it } from "vitest";
import { buildToolHistory } from "@/lib/toolHistory";

describe("buildToolHistory", () => {
  it("keeps the original goal and recent turns in order", () => {
    const out = buildToolHistory([
      { role: "user", content: "原始目标" },
      { role: "assistant", content: "旧结果" },
      { role: "user", content: "最新要求" },
      { role: "assistant", content: "最新答复" },
    ], { maxMessages: 3, maxChars: 1000 });
    expect(out.map(x => x.content)).toEqual(["原始目标", "最新要求", "最新答复"]);
  });

  it("filters tool messages and marks clipped content", () => {
    const out = buildToolHistory([
      { role: "system", content: "do not forward" },
      { role: "tool", content: "secret tool output" },
      { role: "user", content: "a".repeat(400) },
    ], { maxMessages: 4, maxChars: 1000, maxMessageChars: 120 });
    expect(out).toHaveLength(1);
    expect(out[0].content).toContain("中间内容已裁剪");
    expect(out[0].role).toBe("user");
  });

  it("never exceeds the configured message count", () => {
    const out = buildToolHistory(Array.from({ length: 20 }, (_, i) => ({ role: "user", content: String(i) })), { maxMessages: 5 });
    expect(out).toHaveLength(5);
    expect(out.at(-1)?.content).toBe("19");
  });
});

