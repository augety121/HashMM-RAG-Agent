"use strict";
const assert = require("assert");
const B = require("../modules/embedded-browser");

assert.strictEqual(B.normalizeHttpUrl("https://example.com/a"), "https://example.com/a");
assert.strictEqual(B.normalizeHttpUrl("javascript:alert(1)"), null);
assert.strictEqual(B.normalizeHttpUrl("https://u:p@example.com"), null);

assert.deepStrictEqual(
  B.normalizeBounds({ x: -20, y: 40, width: 1000, height: 900 }, { width: 800, height: 600 }),
  { x: 0, y: 40, width: 800, height: 560 },
);
const evidence = B.normalizeEvidenceSelection({
  text: "  可验证   网页证据  ",
  title: " 页面标题 ",
  url: "https://example.com/a#section",
  locator: {
    selector: "html:nth-of-type(1) > body:nth-of-type(1) > p:nth-of-type(2)",
    prefix: "前文",
    suffix: "后文",
    fingerprint: { tag: "P", role: "article", ancestor_path: ["html", "body", "main"] },
    rect: { x: 1.234, y: 2, width: 300, height: 40 },
  },
}, "browser-session-1");
assert.strictEqual(evidence.text, "可验证 网页证据");
assert.strictEqual(evidence.browser_session_id, "browser-session-1");
assert.strictEqual(evidence.selection_hash.length, 64);
assert.ok(evidence.locator.selector.includes("p:nth-of-type(2)"));
assert.strictEqual(evidence.locator.fingerprint.tag, "p");
assert.strictEqual(evidence.locator.fingerprint.role, "article");
assert.deepStrictEqual(evidence.locator.fingerprint.ancestor_path, ["html", "body", "main"]);
assert.ok(B.buildEvidenceLocateScript(evidence.locator).includes("confidence"));
assert.strictEqual(B.normalizeEvidenceLocator({ selector: "body > [onclick=evil]" }).selector, "");
assert.strictEqual(B.describeLoadError(-2, "ERR_FAILED"), "网页连接失败。可能是网络、代理或网站拒绝内嵌访问，请重试或用 Google Chrome 打开。");
assert.strictEqual(B.describeLoadError(-3, "ERR_ABORTED"), "");
assert.ok(!B.describeLoadError(-105, "ERR_NAME_NOT_RESOLVED").includes("ERR_"));
assert.deepStrictEqual(
  B.normalizeBounds({ x: 799, y: 599, width: 0, height: 0 }, { width: 800, height: 600 }),
  { x: 799, y: 599, width: 1, height: 1 },
);

const winCandidates = B.chromeCandidates("win32", { PROGRAMFILES: "C:\\Program Files", LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" });
assert.ok(winCandidates.some(item => item.endsWith("Google\\Chrome\\Application\\chrome.exe")));

(async () => {
  let spawned = null;
  let opened = "";
  const chrome = winCandidates[0];
  const preferred = await B.launchPreferredBrowser("https://example.com/", {
    platform: "win32", env: { PROGRAMFILES: "C:\\Program Files" },
    exists: candidate => candidate === chrome,
    spawn: (exe, args, options) => { spawned = { exe, args, options }; return { unref() {} }; },
    openExternal: async url => { opened = url; },
  });
  assert.strictEqual(preferred.ok, true);
  assert.strictEqual(preferred.browser, "chrome");
  assert.strictEqual(spawned.exe, chrome);
  assert.deepStrictEqual(spawned.args, ["https://example.com/"]);
  assert.strictEqual(opened, "");

  const fallback = await B.launchPreferredBrowser("https://example.com/", {
    platform: "win32", env: {}, exists: () => false,
    openExternal: async url => { opened = url; },
  });
  assert.strictEqual(fallback.ok, true);
  assert.strictEqual(fallback.browser, "system");
  assert.strictEqual(opened, "https://example.com/");

  const denied = await B.launchPreferredBrowser("file:///etc/passwd", { openExternal: async () => {} });
  assert.strictEqual(denied.ok, false);
  console.log("test_embedded_browser: all assertions passed");
})().catch(error => { console.error(error); process.exit(1); });
