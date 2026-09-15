/** test_nav-guard.js — 导航守卫白名单冒烟（纯 node，沙箱可跑）。 */
"use strict";
const assert = require("assert");
const { isNavigationAllowed } = require("../modules/nav-guard");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

const APPDIR = "/opt/hashmm/desktop";
const ctx = { appDir: APPDIR, trustedOrigins: ["http://127.0.0.1:17680", "https://api.hashmm.example"] };

// 1. 本地 app 目录内的 file:// 放行
assert.strictEqual(isNavigationAllowed("file:///opt/hashmm/desktop/app.html", ctx), true);
assert.strictEqual(isNavigationAllowed("file:///opt/hashmm/desktop/remote-host.html?port=1&token=x", ctx), true);
ok("app 目录内 file:// → 允许");

// 2. app 目录外/穿越的 file:// 拒绝
assert.strictEqual(isNavigationAllowed("file:///etc/passwd", ctx), false);
assert.strictEqual(isNavigationAllowed("file:///opt/hashmm/desktop-evil/x.html", ctx), false, "前缀相近的兄弟目录不得命中");
assert.strictEqual(isNavigationAllowed("file:///opt/hashmm/../secret.html", ctx), false);
ok("越界/穿越 file:// → 拒绝");

// 3. 受信 origin 的 http(s) 放行（本地壳层 + 当前后端）
assert.strictEqual(isNavigationAllowed("http://127.0.0.1:17680/chat", ctx), true);
assert.strictEqual(isNavigationAllowed("https://api.hashmm.example/ui", ctx), true);
ok("受信 origin http(s) → 允许");

// 4. 非受信远程 origin 拒绝（含 origin 前缀伪装）
assert.strictEqual(isNavigationAllowed("https://evil.com/", ctx), false);
assert.strictEqual(isNavigationAllowed("https://api.hashmm.example.evil.com/", ctx), false, "子域伪装不得命中");
assert.strictEqual(isNavigationAllowed("http://127.0.0.1:9999/", ctx), false, "同机不同端口非受信");
ok("非受信远程 origin → 拒绝");

// 5. 危险协议一律拒绝
for (const bad of [
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "blob:https://x/abc",
  "vbscript:msgbox(1)",
]) {
  assert.strictEqual(isNavigationAllowed(bad, ctx), false, `${bad} 应拒绝`);
}
ok("javascript:/data:/blob: 等危险协议 → 拒绝");

// 6. about:blank 放行；非法 URL 拒绝
assert.strictEqual(isNavigationAllowed("about:blank", ctx), true);
assert.strictEqual(isNavigationAllowed("not a url", ctx), false);
ok("about:blank 允许 · 非法 URL 拒绝");

// 7. 未提供 appDir 时 file:// 不拦（向后兼容，交调用方决定）
assert.strictEqual(isNavigationAllowed("file:///any/where.html", { trustedOrigins: [] }), true);
ok("未约束 appDir 时 file:// 不拦（兼容）");

console.log(`\ntest_nav-guard: ${pass} 项全部通过`);
