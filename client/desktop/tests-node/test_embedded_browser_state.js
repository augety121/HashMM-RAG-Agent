"use strict";

const assert = require("assert");
const Browser = require("../modules/embedded-browser");

const cold = Browser.blockedNavigationState(
  "",
  "https://unapproved.example/",
  "尚未授权",
);
assert.strictEqual(cold.hasDocument, false);
assert.strictEqual(cold.preservedDocument, false);
assert.strictEqual(cold.error, "尚未授权");

const preserved = Browser.blockedNavigationState(
  "https://cn.bing.com/",
  "https://unapproved.example/",
  "尚未授权",
);
assert.strictEqual(preserved.hasDocument, true);
assert.strictEqual(preserved.preservedDocument, true);
assert.strictEqual(preserved.error, "");
assert.strictEqual(preserved.blockedUrl, "https://unapproved.example/");

console.log("test_embedded_browser_state: all assertions passed");
