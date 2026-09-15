"use strict";
const assert = require("assert");
const T = require("../modules/browser-trajectory");
const ev = [
  { seq: 1, phase: "start", action: "navigate" },
  { seq: 2, phase: "done", action: "navigate", url: "https://example.com", image: "data:image/jpeg;base64,x" },
  { seq: 3, phase: "done", action: "click", index: 2 },
  { seq: 4, phase: "done", action: "read" },
];
assert.strictEqual(T.evaluateTrajectory(ev, ["navigate", "click", "read"], "exact").pass, true);
assert.strictEqual(T.evaluateTrajectory(ev, ["navigate", "read"], "subsequence").pass, true);
assert.strictEqual(T.evaluateTrajectory(ev, ["read", "navigate"], "any_order").pass, true);
assert.strictEqual(T.evaluateTrajectory(ev, ["read", "navigate"], "exact").pass, false);
assert.deepStrictEqual(T.evaluateTrajectory(ev, ["read", "missing"], "any_order").missing, ["missing"]);
const ds = T.buildDatasetCase({ goal: "check page", expected: "navigate,read", mode: "subsequence", events: ev, result: "ok" });
assert.strictEqual(ds.schema, "hashmm.browser-trajectory.v1");
assert.strictEqual(ds.events[0].image, undefined, "export must not embed screenshot payloads");
assert.strictEqual(ds.events[1].screenshot_present, true);

const evidence = T.browserEvidenceBundle([
  { seq: 10, phase: "done", action: "navigate", url: "https://example.com/start" },
  { seq: 11, phase: "done", action: "read", title: "Example page", url: "https://user:pass@example.com/facts?lang=zh&access_token=secret#private", snippet: "项目在 2026 年发布，当前版本为 3。" },
  { seq: 12, phase: "done", action: "read", title: "duplicate", url: "https://example.com/facts?lang=zh&access_token=other", snippet: "重复页面不应产生第二个来源。" },
  { seq: 13, phase: "error", action: "click", url: "https://example.com/facts", error: "blocked" },
], 9);
assert.strictEqual(evidence.sources.length, 1, "only successful, de-duplicated reads are factual sources");
assert.strictEqual(evidence.trace.length, 4, "non-read actions remain trajectory evidence");
assert.strictEqual(evidence.sources[0].section, "https://example.com/facts?lang=zh");
assert.ok(!JSON.stringify(evidence.sources).includes("secret"), "persisted evidence must redact secrets");
assert.ok(!JSON.stringify(evidence.sources).includes("user:pass"), "persisted evidence must redact URL credentials");
const cited = T.attachEvidenceCitations(
  "项目版本是 3 [99]，详见 https://user:pass@example.com/facts?lang=zh&access_token=secret#private。",
  evidence,
);
assert.ok(cited.includes("https://example.com/facts?lang=zh [1]"));
assert.ok(!cited.includes("[99]"), "model-authored citations are not execution evidence");
assert.ok(!cited.includes("secret"));
assert.ok(cited.includes("浏览证据\n[1] Example page"));
console.log("test_browser_trajectory: all assertions passed");
