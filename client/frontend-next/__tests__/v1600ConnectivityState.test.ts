import { describe, expect, it } from "vitest";
import { createConnectivityTracker } from "@/lib/connectivityState";

describe("V1600 connectivity hysteresis", () => {
  it("requires consecutive failures before declaring an outage", () => {
    const tracker = createConnectivityTracker(2);
    expect(tracker.failure()).toBe(false);
    expect(tracker.failure()).toBe(true);
  });

  it("clears the failure streak on any real response", () => {
    const tracker = createConnectivityTracker(2);
    tracker.failure();
    expect(tracker.success()).toBe(true);
    expect(tracker.streak()).toBe(0);
    expect(tracker.failure()).toBe(false);
  });
});
