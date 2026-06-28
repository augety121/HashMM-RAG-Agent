/** test_i18n.js — i18n 国际化模块单测（V100）。
 *  纯逻辑：点分键查找、缺失回退、插值、复数选择。
 *  真实 IO：从目录加载 zh-CN.json/en.json。
 *  运行：node desktop/i18n/test_i18n.js
 */
"use strict";
const assert = require("assert");
const path = require("path");
const I = require("./i18n");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 点分键查找 + 插值
assert.strictEqual(I.getPath({ a: { b: { c: 7 } } }, "a.b.c"), 7);
assert.strictEqual(I.getPath({ a: 1 }, "a.b.c"), undefined);
assert.strictEqual(I.interpolate("你好 {name}！", { name: "Amy" }), "你好 Amy！");
assert.strictEqual(I.interpolate("缺 {x} 保留", {}), "缺 {x} 保留", "未提供的占位符原样");
ok("点分键查找 + 插值（含缺失占位符原样保留）");

// 2. 复数键
assert.strictEqual(I.pluralKey("items", 1, "en"), "items_one");
assert.strictEqual(I.pluralKey("items", 3, "en"), "items_other");
assert.strictEqual(I.pluralKey("items", 1, "zh-CN"), "items_other", "中文无单复数 → 直接 _other");
ok("复数键：英文 _one/_other，中文直接 _other");

// 3. 查找 + 回退 + 漏翻返回键名
{
  const i18n = new I.I18n({
    locale: "zh-CN", fallback: "en",
    resources: {
      "zh-CN": { menu: { newChat: "新对话" }, hello: "你好 {name}" },
      en: { menu: { newChat: "New Chat", settings: "Settings" }, hello: "Hi {name}" },
    },
  });
  assert.strictEqual(i18n.t("menu.newChat"), "新对话", "当前语言命中");
  assert.strictEqual(i18n.t("menu.settings"), "Settings", "当前语言缺 → 回退 en");
  assert.strictEqual(i18n.t("nope.key"), "nope.key", "全缺 → 返回键名，不崩");
  assert.ok(i18n.missing.includes("nope.key"), "漏翻键被记录");
  assert.strictEqual(i18n.t("hello", { name: "李雷" }), "你好 李雷", "命中 + 插值");
  i18n.setLocale("en");
  assert.strictEqual(i18n.t("hello", { name: "Amy" }), "Hi Amy", "切语言生效");
}
ok("查找 + 回退链 + 漏翻返回键名(并记录) + 切语言");

// 4. 复数实际选择
{
  const i18n = new I.I18n({
    locale: "en", fallback: "en",
    resources: { en: { items_one: "{count} item", items_other: "{count} items" } },
  });
  assert.strictEqual(i18n.t("items", { count: 1 }), "1 item");
  assert.strictEqual(i18n.t("items", { count: 5 }), "5 items");
}
ok("复数实际选择 + count 插值");

// 5. 从目录加载真实 locale 文件
{
  const i18n = new I.I18n({ locale: "zh-CN", fallback: "en" });
  const n = i18n.loadDir(__dirname); // 加载本目录的 zh-CN.json / en.json
  assert.ok(n >= 2, "应加载到至少 2 种语言");
  assert.ok(i18n.availableLocales().includes("zh-CN") && i18n.availableLocales().includes("en"));
  assert.strictEqual(i18n.t("menu.newChat"), "新对话");
  assert.strictEqual(i18n.t("installer.install"), "立即安装");
  i18n.setLocale("en");
  assert.strictEqual(i18n.t("installer.install"), "Install Now");
  assert.strictEqual(i18n.t("items", { count: 1 }), "1 item", "en 复数 _one");
}
ok("从目录加载 zh-CN/en 真实文件 + 跨语言取值 + 复数");

console.log(`\ntest_i18n: ${pass} 项全部通过`);
