"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");

const classifier = main.match(/function _kindNeedsBrowserLock\(kind\) \{([\s\S]*?)\n\}/);
assert.ok(classifier, "resource lane classifier must exist");
assert.match(classifier[1], /browser_use/);
assert.match(classifier[1], /computer_use/);
assert.doesNotMatch(classifier[1], /kind === "auto"/);
assert.doesNotMatch(classifier[1], /kind === "file"/);
assert.match(main, /await _withBrowserLock\(\(\) => _handleBrowserAgent\(r2/,
  "auto-routed browser action must acquire the physical-resource lock at execution time");
assert.match(main, /else \{\s*\/\/ file \/ task \/ auto[\s\S]*?await _handleAuto\(r, goal/,
  "ordinary auto and file work must not hold the physical-resource lock while routing");
assert.match(main, /function _fetchWithDeadline/,
  "dispatch control-plane calls must have a bounded deadline");
assert.match(main, /_fetchWithDeadline\(base \+ "\/api\/dispatch\/poll/,
  "dispatch poll must use the bounded request helper");
assert.match(main, /"cache", "accounts", `\$\{identity\}\.cache`/,
  "encrypted desktop replicas must use separate per-account files");

console.log("test_v1500_resource_lanes: passed");
