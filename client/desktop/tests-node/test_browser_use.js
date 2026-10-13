"use strict";
const assert = require("assert");
const B = require("../modules/browser-use");

assert.strictEqual(B.validateBrowserAction({ action: "navigate", url: "file:///x" }).ok, false);
assert.strictEqual(B.validateBrowserAction({ action: "navigate", url: "example.com" }).plan.url, "https://example.com");
assert.strictEqual(B.validateBrowserAction({ action: "click" }).ok, false);
assert.strictEqual(B.assessBrowserAction({ action: "click", index: 1 }, { cuReadOnly: true }).decision, "deny");
assert.strictEqual(B.assessBrowserAction(
  { action: "type", index: 1, text: "hidden" }, {}, { element: { type: "password", text: "Password" } }
).decision, "confirm");
assert.strictEqual(B.assessBrowserAction(
  { action: "click", index: 2 }, {}, { element: { type: "button", text: "立即付款" } }
).decision, "confirm");
assert.strictEqual(B.assessBrowserAction(
  { action: "click", index: 3 }, {}, { element: { type: "button", text: "下一页" } }
).decision, "allow");
console.log("test_browser_use: all assertions passed");
