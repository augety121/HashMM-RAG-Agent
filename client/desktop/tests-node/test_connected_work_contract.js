"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const main = fs.readFileSync(path.join(root, "main.js"), "utf8");
const preload = fs.readFileSync(path.join(root, "preload.js"), "utf8");

assert(main.includes('ipcMain.on("remote-quality-telemetry"'));
assert(main.includes("_remoteQualitySource(evt)"));
assert(main.includes('schema !== "hashmm.remote-quality.v1"'));
assert(main.includes("const quality = _latestRemoteQuality()"));
assert(!main.includes("remote-quality-telemetry\", (_evt"), "remote telemetry must validate its sender");

assert(preload.includes('officeHandoffAcknowledge: (id, sha256)'));
assert(main.includes("acknowledge(p.id, p.sha256)"));
assert(main.includes('typeof payload === "object"'));

console.log("connected-work-contract: ok");
