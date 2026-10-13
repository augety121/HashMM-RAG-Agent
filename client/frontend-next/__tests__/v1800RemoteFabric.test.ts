import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const repo = path.resolve(__dirname, "..", "..");
const read = (...parts: string[]) => fs.readFileSync(path.join(repo, ...parts), "utf8");

describe("V1800 Remote Fabric 4.0 contracts", () => {
  it("desktop main process owns the one-time ticket and persistent host socket", () => {
    const main = read("desktop", "main.js");
    const host = read("desktop", "remote-host.html");
    const supervisor = read("desktop", "services", "remote-host-supervisor.js");
    expect(main).toContain("/api/remote/v4/bootstrap");
    expect(main).toContain("/api/remote/v4/socket-ticket");
    expect(main).not.toContain('return `${proto}//${u.host}/api/remote/ws`');
    expect(main).toContain('ipcMain.on("remote-host-signal-send"');
    expect(supervisor).toContain('this._publish("registered"');
    expect(supervisor).toContain('type: "heartbeat"');
    expect(host).toContain("remote-host-signal-message");
    expect(host).not.toContain("/api/remote/v4/socket-ticket");
  });

  it("desktop host supervision recovers independently of the token refresh cadence", () => {
    const app = read("frontend-next", "components", "App.tsx");
    expect(app).toContain("const hostWatchdog = setInterval(arm, 15 * 1000)");
    expect(app).toContain("clearInterval(hostWatchdog)");
    expect(app).toContain("clearInterval(timer)");
  });

  it("backend directory is owner scoped and generation fenced", () => {
    const registry = read("hashmm", "api", "remote_devices.py");
    const hub = read("hashmm", "api", "remote_hub.py");
    expect(registry).toContain("PRIMARY KEY(user_id,device_id)");
    expect(registry).toContain("AND lease_id=? AND generation=?");
    expect(hub).toContain('"type": "deviceReplaced"');
    expect(hub).toContain('"reason": "stale_generation"');
  });

  it("keeps signaling on the server while media is direct first", () => {
    const host = read("desktop", "remote-host.html");
    const viewer = read("desktop", "remote-viewer.html");
    const hub = read("hashmm", "api", "remote_hub.py");
    expect(host).toContain("scheduleRelayFallback");
    expect(host).not.toContain("startRelayPush();   // 同时把画面");
    expect(viewer).toContain("scheduleLegacyFallback");
    expect(hub).toContain('"transportPolicy": "ice-direct-turn-fallback"');
    expect(hub).toContain('"transportMode": transport_mode');
  });
});
