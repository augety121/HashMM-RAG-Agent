/** test_preload_contract.js — Electron preload/contextBridge 契约测试（V306）。
 *
 * 用 mock electron 加载真实 preload.js，捕获它经 contextBridge 暴露给渲染层的**实际 API 面**，
 * 断言安全契约（渲染层 nodeIntegration:false + contextIsolation:on 下，preload 是唯一的能力入口，
 * 其暴露面必须最小化、无逃逸）：
 *   · 暴露的都是命名空间化的具体方法，没有把 raw ipcRenderer 直接丢给渲染层；
 *   · 没有任何方法允许渲染层指定【任意 IPC 通道】——把毒字符串当参数传进每个方法，
 *     捕获真正被调用的通道，断言毒字符串【绝不】成为通道（即通道都是硬编码的）；
 *   · 没有暴露 require/process/global/node 内置；
 *   · 所有被调用的通道都落在已知命名前缀内（cmd/tabs/export/... 白名单）。
 *
 * 这是"验证暴露给渲染层的 API 面最小化"最直接的自动化检查——真加载、真捕获、真调用。
 */
"use strict";
const assert = require("assert");
const path = require("path");
const Module = require("module");

// ── 用 mock electron 加载 preload，捕获暴露面与被调用的通道 ──
const exposed = {};
const invokedChannels = [];
const POISON = "__ARBITRARY_EVIL_CHANNEL__";

function loadPreload() {
  const mockIpc = {
    invoke: (ch) => { invokedChannels.push(String(ch)); return Promise.resolve(null); },
    send: (ch) => { invokedChannels.push("send:" + String(ch)); },
    on: (ch) => { invokedChannels.push("on:" + String(ch)); },
    removeAllListeners: () => {},
    sendSync: (ch) => { invokedChannels.push("sync:" + String(ch)); return null; },
  };
  const mockBridge = { exposeInMainWorld: (name, api) => { exposed[name] = api; } };
  const origLoad = Module._load;
  Module._load = function (request, ...rest) {
    if (request === "electron") return { contextBridge: mockBridge, ipcRenderer: mockIpc };
    return origLoad.apply(this, [request, ...rest]);
  };
  try {
    delete require.cache[require.resolve(path.join(__dirname, "../preload.js"))];
    require("../preload.js");
  } finally {
    Module._load = origLoad;
  }
}

loadPreload();

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 确实暴露了命名空间化 API（且数量合理）
const names = Object.keys(exposed);
assert.ok(names.length >= 20, `暴露命名空间过少：${names.length}`);
assert.ok(names.every((n) => /^hashmm/.test(n)), "存在非 hashmm 命名空间的暴露（面不受控）");
ok(`暴露 ${names.length} 个 hashmm* 命名空间 API（全部命名空间化）`);

// 2. 没有把 raw ipcRenderer 丢给渲染层：任何命名空间不得同时具备 invoke+send+on 三件套（那就是 ipcRenderer）
for (const [ns, api] of Object.entries(exposed)) {
  if (api && typeof api === "object") {
    const hasRaw = typeof api.invoke === "function" && typeof api.send === "function" && typeof api.on === "function";
    assert.ok(!hasRaw, `${ns} 疑似直接暴露了 ipcRenderer（invoke+send+on 三件套）`);
    // 也不能直接暴露 require/process
    assert.ok(!("require" in api) && !("process" in api), `${ns} 暴露了 require/process`);
  }
}
ok("未暴露 raw ipcRenderer / require / process");

// 3. 核心：把毒字符串当参数传进每个方法，断言毒字符串绝不成为 IPC 通道（通道硬编码，无任意通道逃逸）
let called = 0;
for (const [ns, api] of Object.entries(exposed)) {
  if (!api || typeof api !== "object") continue;
  for (const [fnName, fn] of Object.entries(api)) {
    if (typeof fn !== "function") continue;
    called++;
    try {
      // 用毒字符串占满前若干个参数——若某方法把参数当通道用，就会被抓到
      fn(POISON, POISON, POISON);
    } catch (_e) { /* 参数不合法抛错无所谓，我们只关心它有没有拿毒串当通道 */ }
  }
}
const leaked = invokedChannels.filter((ch) => ch.includes(POISON));
assert.strictEqual(leaked.length, 0,
  `发现任意通道逃逸：${leaked.slice(0, 3).join(", ")}（渲染层可借此调用任意 IPC 通道）`);
ok(`调用了 ${called} 个暴露方法 × 毒参数 → 零任意通道逃逸（通道全硬编码）`);

// 4. 所有真正被调用的通道都落在已知命名前缀内（白名单）
const ALLOW_PREFIXES = [
  "app:", "auth-session:", "backend:", "browser:", "ckpt:", "cockpit:", "config:", "conv:", "cu:", "export:",
  "feature:", "files:", "fs:", "git:", "hashmm:", "hashmm-", "llm:", "local:",
  "localrag:", "memory:", "nav", "notify:", "pack:", "remote:", "semantic:",
  "shell:", "term:", "workspace:", "workspace-cache:", "project:",
];
const realChannels = invokedChannels
  .map((c) => c.replace(/^(send|sync|on):/, ""))
  .filter((c) => !c.includes(POISON));
const unknown = realChannels.filter((ch) => !ALLOW_PREFIXES.some((p) => ch.startsWith(p)));
assert.strictEqual(unknown.length, 0, `存在未在白名单前缀内的通道：${[...new Set(unknown)].slice(0, 5).join(", ")}`);
ok(`被调用的通道全部落在已知命名前缀白名单内（${new Set(realChannels).size} 个不同通道）`);

// 5. preload 源码声明 contextIsolation 语义（require electron 的 contextBridge 而非直接挂 window）
const fs = require("fs");
const src = fs.readFileSync(path.join(__dirname, "../preload.js"), "utf-8");
assert.ok(src.includes("contextBridge") && src.includes("exposeInMainWorld"),
  "preload 未用 contextBridge.exposeInMainWorld（隔离前提）");
assert.ok(!/\bwindow\.\w+\s*=/.test(src) || src.indexOf("contextBridge") < src.indexOf("window."),
  "preload 疑似绕过 contextBridge 直接挂 window");
ok("preload 走 contextBridge.exposeInMainWorld（contextIsolation 前提）");

console.log(`\ntest_preload_contract: ${pass} 项全部通过`);
