/** test_capability-pack.js — 沙箱可跑的冒烟测试（node test_capability-pack.js）。
 * 用内存假 fs / 假 fetch / 假解压，完整走通：sha256 校验、清单合并、下载安装、
 * 校验失败、幂等缓存、force 重装、进度回调。 */
"use strict";
const assert = require("assert");
const crypto = require("crypto");
const { CapabilityPackManager, sha256Hex, verifySha256, parseManifest, validatePackSource } =
  require("./capability-pack.js");

let passed = 0;
function ok(name) { passed++; console.log("  ok -", name); }

// ---- 内存假 fs ----
function makeFakeFs() {
  const files = new Map();     // path -> string/Buffer
  const dirs = new Set();
  return {
    files, dirs,
    existsSync: (p) => files.has(p) || dirs.has(p),
    mkdirSync: (p) => { dirs.add(p); },
    writeFileSync: (p, data) => { files.set(p, data); },
    readFileSync: (p) => {
      if (!files.has(p)) { const e = new Error("ENOENT " + p); throw e; }
      return files.get(p);
    },
    rmSync: (p) => {
      files.delete(p);
      for (const k of [...files.keys()]) if (k.startsWith(p)) files.delete(k);
      dirs.delete(p);
    },
  };
}

// ---- 1) 纯函数：sha256 / verify ----
(function () {
  const buf = Buffer.from("hashmm-pack-bytes");
  const h = crypto.createHash("sha256").update(buf).digest("hex");
  assert.strictEqual(sha256Hex(buf), h);
  assert.strictEqual(verifySha256(buf, h).ok, true);
  assert.strictEqual(verifySha256(buf, "DEAD" + h.slice(4)).ok, false);
  assert.strictEqual(verifySha256(buf, "").ok, true);          // 空 -> 跳过
  assert.strictEqual(verifySha256(buf, "").skipped, true);
  ok("sha256Hex + verifySha256 (match / mismatch / skip)");
})();

// ---- 1b) 下载源策略：远程必须 HTTPS + 64位 SHA；loopback 可用于本机调试 ----
(function () {
  assert.strictEqual(validatePackSource({ url: "http://127.0.0.1:17680/a.zip", sha256: "" }).ok, true);
  assert.strictEqual(validatePackSource({ url: "http://cdn.example/a.zip", sha256: "0".repeat(64) }).ok, false);
  assert.strictEqual(validatePackSource({ url: "https://cdn.example/a.zip", sha256: "" }).ok, false);
  assert.strictEqual(validatePackSource({ url: "https://cdn.example/a.zip", sha256: "a".repeat(64) }).ok, true);
  ok("能力包源策略（远程 HTTPS + SHA-256，loopback 调试例外）");
})();

// ---- 2) 清单合并 ----
(function () {
  const m = parseManifest({ ocr: { version: "v9", sha256: "abc" }, extra: { url: "u" } });
  assert.strictEqual(m.ocr.version, "v9");
  assert.strictEqual(m.ocr.sha256, "abc");
  assert.ok(m["python-runtime"], "默认包保留");
  assert.strictEqual(m.extra.id, "extra");                      // 注入 id
  ok("parseManifest 默认+覆盖+注入 id");
})();

// ---- 3) 安装成功路径 ----
(async function () {
  const fs = makeFakeFs();
  const payload = Buffer.from("ZIPDATA-python-runtime");
  const sha = sha256Hex(payload);
  let extracted = null;
  const progress = [];
  const mgr = new CapabilityPackManager({
    userData: "/ud",
    manifest: parseManifest({ "python-runtime": { sha256: sha } }),
    fs,
    fetchBuf: async (url, { onProgress }) => { onProgress(50); return payload; },
    extract: async (zip, dest) => { extracted = { zip, dest }; },
  });

  assert.strictEqual(mgr.isInstalled("python-runtime"), false);
  const r = await mgr.install("python-runtime", { onProgress: (p) => progress.push(p) });
  assert.strictEqual(r.ok, true, "install ok: " + JSON.stringify(r));
  assert.ok(extracted, "extract 被调用");
  assert.ok(extracted.dest.endsWith("packs/python-runtime") ||
            extracted.dest.endsWith("packs\\python-runtime"));
  assert.ok(progress.includes(100), "进度到 100");
  assert.strictEqual(mgr.isInstalled("python-runtime"), true, "标记已写、版本匹配");
  assert.ok(mgr.installedPath("python-runtime"), "installedPath 非空");
  ok("install 成功路径（下载->校验->解压->标记）");

  // ---- 4) 幂等：再装直接 cached ----
  let fetched2 = false;
  mgr._fetchBuf = async () => { fetched2 = true; return payload; };
  const r2 = await mgr.install("python-runtime");
  assert.strictEqual(r2.cached, true, "第二次应命中缓存");
  assert.strictEqual(fetched2, false, "缓存命中时不应再下载");
  ok("install 幂等（已装命中缓存，不重复下载）");

  // ---- 5) force 重装会重新下载 ----
  let fetched3 = false;
  mgr._fetchBuf = async () => { fetched3 = true; return payload; };
  const r3 = await mgr.install("python-runtime", { force: true });
  assert.strictEqual(r3.ok, true);
  assert.strictEqual(fetched3, true, "force 应重新下载");
  ok("install force 重新下载");
})();

// ---- 6) sha256 不匹配 -> 失败、不留标记 ----
(async function () {
  const fs = makeFakeFs();
  const mgr = new CapabilityPackManager({
    userData: "/ud2",
    manifest: parseManifest({ ocr: { sha256: "0".repeat(64) } }),  // 故意错的 sha
    fs,
    fetchBuf: async () => Buffer.from("tampered-bytes"),
    extract: async () => { throw new Error("不该解压"); },
  });
  const r = await mgr.install("ocr");
  assert.strictEqual(r.ok, false, "校验失败应 ok:false");
  assert.ok(/校验失败/.test(r.error), "错误信息含校验失败");
  assert.strictEqual(mgr.isInstalled("ocr"), false, "失败不留标记");
  ok("install sha256 不匹配 -> 失败且不留半成品");
})();

// ---- 7) 未知包 / 下载异常 ----
(async function () {
  const mgr = new CapabilityPackManager({ userData: "/ud3", fs: makeFakeFs() });
  const r = await mgr.install("nope");
  assert.strictEqual(r.ok, false);
  assert.ok(/未知能力包/.test(r.error));

  const mgr2 = new CapabilityPackManager({
    userData: "/ud4", fs: makeFakeFs(),
    manifest: parseManifest({ ocr: { sha256: "" } }),
    fetchBuf: async () => { throw new Error("网络炸了"); },
  });
  const r2 = await mgr2.install("ocr");
  assert.strictEqual(r2.ok, false);
  assert.ok(/安装失败/.test(r2.error));
  ok("install 未知包 + 下载异常都安全返回");
})();

setTimeout(() => {
  console.log(`\n=== capability-pack: ${passed} assertions/groups passed ===`);
}, 50);
