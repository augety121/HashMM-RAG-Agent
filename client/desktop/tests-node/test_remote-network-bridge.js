"use strict";
const assert = require("assert");
const bridge = require("../services/remote-network-bridge");

assert.strictEqual(bridge.normalizeTarget("10.144.2.8"), "10.144.2.8");
assert.strictEqual(bridge.normalizeTarget("office-pc"), "office-pc");
assert.strictEqual(bridge.normalizePort("3389"), 3389);
for (const value of ["", "https://10.0.0.2", "10.0.0.2:3389", "pc && calc", "pc name"]) {
  assert.throws(() => bridge.normalizeTarget(value));
}
for (const value of [0, 65536, 1.5, "x"]) {
  assert.throws(() => bridge.normalizePort(value));
}

const adapters = bridge.privateAdapters({
  Ethernet: [{ family: "IPv4", address: "192.168.1.2", internal: false }],
  EasyTier: [
    { family: "IPv4", address: "10.144.2.8", internal: false, cidr: "10.144.2.8/24", mac: "00:11:22:33:44:55" },
    { family: "IPv6", address: "fd00::1", internal: false },
  ],
  "Wintun Userspace Tunnel": [{ family: "IPv4", address: "10.2.0.3", internal: false }],
});
assert.deepStrictEqual(adapters.map((x) => x.address), ["10.144.2.8", "10.2.0.3"]);
const bridgeStatus = bridge.status({
  networkInterfaces: () => ({ EasyTier: [{ family: "IPv4", address: "10.144.2.8", internal: false }] }),
  env: { PATH: "", ProgramFiles: "Z:\\missing", "ProgramFiles(x86)": "Z:\\missing", LOCALAPPDATA: "Z:\\missing" },
});
assert.strictEqual(bridgeStatus.schema, "hashmm.remote-network-bridge.v2");
assert.strictEqual(bridgeStatus.mode, "external-overlay-ready");
assert.strictEqual(bridgeStatus.easyTier.active, true);
assert.strictEqual(bridgeStatus.easyTier.bundled, false);
assert.deepStrictEqual(bridgeStatus.routingPolicy.slice(0, 2), ["webrtc-direct", "turn-relay"]);

const launches = [];
const fakeSpawn = (file, args, options) => {
  launches.push({ file, args, options });
  return { unref() {} };
};
const env = {
  SystemRoot: "C:\\Windows",
  PATH: "",
  ProgramFiles: "C:\\Program Files",
  "ProgramFiles(x86)": "C:\\Program Files (x86)",
  LOCALAPPDATA: "C:\\Users\\test\\AppData\\Local",
};
const originalExists = require("fs").existsSync;
const originalStat = require("fs").statSync;
const fs = require("fs");
fs.existsSync = (p) => /mstsc\.exe$|Moonlight\.exe$/i.test(String(p));
fs.statSync = () => ({ isFile: () => true });
try {
  const rdp = bridge.launchRdp("10.144.2.9", { port: 3390 }, { env, spawn: fakeSpawn });
  assert.strictEqual(rdp.ok, true);
  assert.deepStrictEqual(launches[0].args, ["/v:10.144.2.9:3390"]);
  assert.strictEqual(launches[0].options.shell, false);

  const moonlight = bridge.launchMoonlight("gaming-pc", { app: "Desktop" }, { env, spawn: fakeSpawn });
  assert.strictEqual(moonlight.ok, true);
  assert.deepStrictEqual(launches[1].args.slice(-3), ["stream", "gaming-pc", "Desktop"]);
  assert.strictEqual(launches[1].options.shell, false);
} finally {
  fs.existsSync = originalExists;
  fs.statSync = originalStat;
}

console.log("remote network bridge tests passed");
