/**
 * desktop/i18n/i18n.js — 轻量国际化模块（V100，对标 Marvis 的 i18n/）。
 *
 * 大厂客户端都有的国际化基建，与框架无关：
 *   - 多语言资源（zh-CN / en 等），点分键查找（"menu.newChat"）
 *   - 缺失回退：当前语言 → 回退语言 → 返回键本身（不崩、便于定位漏翻）
 *   - 插值：t("hello", {name}) → "你好 {name}" 替换
 *   - 复数：t("items", {count}) → 选 _one/_other（英文）或直接 _other（中文无复数）
 *   - 从目录加载 zh-CN.json/en.json
 *
 * 查找/插值/复数是纯函数，可单测；目录加载用 node fs。
 */
"use strict";
const fs = require("fs");
const path = require("path");

/** 点分键取值：get({a:{b:1}}, "a.b") → 1；缺失 → undefined。 */
function getPath(obj, key) {
  if (!obj) return undefined;
  let cur = obj;
  for (const part of String(key).split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[part];
  }
  return cur;
}

/** 插值：把 "{name}" 替换为 params.name；未提供的占位符原样保留。 */
function interpolate(str, params) {
  if (typeof str !== "string" || !params) return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

/**
 * 复数选择：英文等用 count===1 ? _one : _other；中文（无复数形态）直接 _other。
 * 资源里可写 { items_one:"{count} item", items_other:"{count} items" }。
 */
function pluralKey(baseKey, count, locale) {
  const noPlural = /^zh|^ja|^ko/.test(locale || ""); // 中日韩无单复数形态
  if (noPlural) return baseKey + "_other";
  return count === 1 ? baseKey + "_one" : baseKey + "_other";
}

class I18n {
  /**
   * @param {object} opts
   * @param {string} opts.locale 当前语言（如 "zh-CN"）
   * @param {string} opts.fallback 回退语言（默认 "en"）
   * @param {object} opts.resources { "zh-CN": {...}, "en": {...} }
   */
  constructor(opts = {}) {
    this.locale = opts.locale || "zh-CN";
    this.fallback = opts.fallback || "en";
    this.resources = opts.resources || {};
    this.missing = []; // 记录漏翻的键，便于补
  }

  setLocale(locale) { this.locale = locale; return this; }
  addResource(locale, dict) { this.resources[locale] = Object.assign({}, this.resources[locale], dict); return this; }
  availableLocales() { return Object.keys(this.resources); }

  /** 取一个键（支持复数 + 插值），缺失回退到 fallback，再缺失返回键本身。 */
  t(key, params) {
    let lookupKey = key;
    if (params && typeof params.count === "number") {
      const pk = pluralKey(key, params.count, this.locale);
      // 有复数变体才用，否则退回原键
      if (getPath(this.resources[this.locale], pk) !== undefined ||
          getPath(this.resources[this.fallback], pk) !== undefined) {
        lookupKey = pk;
      }
    }
    let val = getPath(this.resources[this.locale], lookupKey);
    if (val === undefined) val = getPath(this.resources[this.fallback], lookupKey);
    if (val === undefined) {
      if (!this.missing.includes(key)) this.missing.push(key);
      return key; // 漏翻：返回键名，不崩
    }
    return interpolate(val, params);
  }

  /** 从目录加载 *.json（文件名即 locale）。返回加载了多少种语言。 */
  loadDir(dir) {
    let n = 0;
    try {
      for (const f of fs.readdirSync(dir)) {
        if (!f.endsWith(".json")) continue;
        const locale = f.replace(/\.json$/, "");
        try { this.resources[locale] = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")); n++; }
        catch (_) { /* 坏文件跳过 */ }
      }
    } catch (_) { /* 目录不存在 */ }
    return n;
  }
}

function createI18n(opts) { return new I18n(opts); }

module.exports = { createI18n, I18n, getPath, interpolate, pluralKey };
