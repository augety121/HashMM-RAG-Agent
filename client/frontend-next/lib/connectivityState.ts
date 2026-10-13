export type ConnectivityTracker = {
  success(): boolean;
  failure(): boolean;
  streak(): number;
};

/** Consecutive-failure hysteresis for the shared backend connection state. */
export function createConnectivityTracker(threshold = 2): ConnectivityTracker {
  const required = Math.max(1, Math.floor(threshold));
  let failures = 0;
  return {
    success() {
      failures = 0;
      return true;
    },
    failure() {
      failures += 1;
      return failures >= required;
    },
    streak() {
      return failures;
    },
  };
}
