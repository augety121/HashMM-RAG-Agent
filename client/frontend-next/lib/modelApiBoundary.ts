import type { ModelConfig, ModelWireApi, ProviderSpec } from "./types";

const WIRE_APIS = new Set<ModelWireApi>([
  "chat_completions",
  "responses",
  "anthropic_messages",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function wire(value: unknown, fallback: ModelWireApi): ModelWireApi {
  return typeof value === "string" && WIRE_APIS.has(value as ModelWireApi)
    ? value as ModelWireApi
    : fallback;
}

/**
 * Normalize model-list responses at the HTTP boundary.  Older HashMM servers
 * returned a bare array while some compatible gateways wrap it in `models`.
 * Invalid/error objects must never become UI state.
 */
export function normalizeModelList(payload: unknown): ModelConfig[] {
  const body = record(payload);
  const items = Array.isArray(payload)
    ? payload
    : body && Array.isArray(body.models) ? body.models : [];
  return items.filter((item): item is ModelConfig => {
    const row = record(item);
    return !!row && typeof row.id === "string";
  });
}

/**
 * Make provider discovery backward compatible and fail closed.  The admin UI
 * has its own curated fallback catalog, so an unknown payload is represented
 * by an empty list rather than partially trusted fields.
 */
export function normalizeProviderResponse(payload: unknown): { providers: ProviderSpec[] } {
  const body = record(payload);
  const items = Array.isArray(payload)
    ? payload
    : body && Array.isArray(body.providers) ? body.providers : [];

  const providers = items.flatMap(item => {
    const row = record(item);
    if (!row || typeof row.id !== "string" || !row.id.trim()) return [];
    const rawWires = Array.isArray(row.wire_apis) ? row.wire_apis : [];
    const wireApis = rawWires
      .filter((value): value is ModelWireApi => typeof value === "string" && WIRE_APIS.has(value as ModelWireApi));
    const fallbackWire: ModelWireApi = row.id === "anthropic" ? "anthropic_messages" : "chat_completions";
    if (!wireApis.length) wireApis.push(fallbackWire);
    const defaultWire = wire(row.default_wire_api, wireApis[0]);
    if (!wireApis.includes(defaultWire)) wireApis.unshift(defaultWire);
    const rawCapabilities = record(row.capabilities);

    return [{
      id: row.id,
      name: text(row.name, row.id),
      base_url: text(row.base_url),
      wire_apis: wireApis,
      default_wire_api: defaultWire,
      auth: text(row.auth, "bearer"),
      local: bool(row.local, false),
      base_url_required: bool(row.base_url_required, row.id === "custom"),
      endpoint_note: text(row.endpoint_note),
      model_hints: Array.isArray(row.model_hints)
        ? row.model_hints.filter((value): value is string => typeof value === "string")
        : [],
      capabilities: {
        tools: bool(rawCapabilities?.tools, false),
        streaming: bool(rawCapabilities?.streaming, true),
        vision: bool(rawCapabilities?.vision, false),
        json_schema: bool(rawCapabilities?.json_schema, false),
        model_discovery: bool(rawCapabilities?.model_discovery, false),
        native_state: bool(rawCapabilities?.native_state, false),
        prompt_cache_telemetry: bool(rawCapabilities?.prompt_cache_telemetry, false),
        reasoning_control: text(rawCapabilities?.reasoning_control),
      },
    } satisfies ProviderSpec];
  });

  return { providers };
}
