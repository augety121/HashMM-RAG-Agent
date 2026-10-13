import { describe, expect, it } from "vitest";
import { normalizeModelList, normalizeProviderResponse } from "@/lib/modelApiBoundary";

describe("model API boundary", () => {
  it("accepts bare and wrapped model lists and rejects error objects", () => {
    const model = { id: "m1", name: "M", provider: "openai" };
    expect(normalizeModelList([model])).toHaveLength(1);
    expect(normalizeModelList({ models: [model] })).toHaveLength(1);
    expect(normalizeModelList({ detail: "old server" })).toEqual([]);
  });

  it("does not throw when a compatible server omits providers", () => {
    expect(normalizeProviderResponse({})).toEqual({ providers: [] });
    expect(normalizeProviderResponse({ providers: undefined })).toEqual({ providers: [] });
  });

  it("fills safe provider defaults for older server payloads", () => {
    const result = normalizeProviderResponse([{ id: "anthropic", name: "Claude" }]);
    expect(result.providers[0].wire_apis).toEqual(["anthropic_messages"]);
    expect(result.providers[0].capabilities.streaming).toBe(true);
    expect(result.providers[0].capabilities.native_state).toBe(false);
    expect(result.providers[0].capabilities.prompt_cache_telemetry).toBe(false);
    expect(result.providers[0].capabilities.reasoning_control).toBe("");
    expect(result.providers[0].model_hints).toEqual([]);
  });

  it("preserves the provider capability contract used by model setup", () => {
    const result = normalizeProviderResponse({
      providers: [{
        id: "openai",
        name: "OpenAI",
        wire_apis: ["responses", "chat_completions"],
        default_wire_api: "responses",
        capabilities: {
          tools: true,
          streaming: true,
          vision: true,
          json_schema: true,
          model_discovery: true,
          native_state: true,
          prompt_cache_telemetry: true,
          reasoning_control: "responses",
        },
      }],
    });
    expect(result.providers[0].capabilities).toMatchObject({
      native_state: true,
      prompt_cache_telemetry: true,
      reasoning_control: "responses",
    });
  });
});
