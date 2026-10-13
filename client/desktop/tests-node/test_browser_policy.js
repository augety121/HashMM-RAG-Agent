"use strict";
const assert = require("assert");
const os = require("os");
const fs = require("fs");
const path = require("path");
const P = require("../modules/browser-policy");

assert.strictEqual(P.parseBrowserUrl("example.com/a#x").host, "example.com");
assert.strictEqual(P.parseBrowserUrl("file:///etc/passwd").ok, false);
assert.strictEqual(P.parseBrowserUrl("https://u:p@example.com").ok, false);
assert.strictEqual(P.parseBrowserUrl("http://169.254.169.254/latest/meta-data").ok, false);
assert.strictEqual(P.parseBrowserUrl("http://127.0.0.1:3000").ok, true, "localhost preview remains possible with an explicit site decision");

let policy = P.normalizePolicy({ allow: ["EXAMPLE.com", "example.com"], block: ["bad.test"] });
assert.deepStrictEqual(policy.allow, ["example.com"]);
assert.strictEqual(P.decideNavigation("https://example.com", policy).decision, "allow");
assert.strictEqual(P.decideNavigation("https://bad.test", policy).decision, "deny");
assert.strictEqual(P.decideNavigation("https://new.test", policy).decision, "ask");
assert.strictEqual(P.decideNavigation("https://once.test", policy, new Set(["once.test"])).decision, "allow");
assert.strictEqual(P.isTrustedServiceRedirect("https://www.bing.com/", "https://cn.bing.com/"), true);
assert.strictEqual(P.isTrustedServiceRedirect("https://bing.com/", "https://www.bing.com/"), true);
assert.strictEqual(P.isTrustedServiceRedirect("https://www.bing.com/", "https://evil.example/"), false);
assert.strictEqual(P.isTrustedServiceRedirect("https://foo.github.io/", "https://bar.github.io/"), false, "multi-tenant siblings must not be merged");
assert.strictEqual(P.isTrustedServiceRedirect("https://example.com/", "https://evil.example.com/"), false, "root to arbitrary child needs a new decision");
policy = P.setHostDecision(policy, "new.test", "allow");
assert.ok(policy.allow.includes("new.test"));
policy = P.setHostDecision(policy, "new.test", "block");
assert.ok(!policy.allow.includes("new.test") && policy.block.includes("new.test"));

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-browser-policy-"));
const file = path.join(dir, "policy.json");
const store = new P.BrowserPolicyStore(file);
store.set("persist.test", "allow");
assert.ok(new P.BrowserPolicyStore(file).load().allow.includes("persist.test"));
assert.ok(!fs.existsSync(file + ".tmp-" + process.pid), "atomic temp file must not remain");
fs.rmSync(dir, { recursive: true, force: true });
console.log("test_browser_policy: all assertions passed");
