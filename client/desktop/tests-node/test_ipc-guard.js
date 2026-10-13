/** test_ipc-guard.js — IPC 守卫冒烟（纯 node）。 */
"use strict";
const assert = require("assert");
const G = require("../modules/ipc-guard");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

const APPDIR = "/opt/hashmm/desktop";
const ctx = { appDir: APPDIR, trustedOrigins: ["http://127.0.0.1:17680"] };
const appPage = "file:///opt/hashmm/desktop/app.html";
const hiddenPage = "file:///opt/hashmm/desktop/remote-host.html?port=1";
const shellPage = "http://127.0.0.1:17680/";
const evilPage = "https://evil.com/x";

// 1. 敏感通道分类
for (const c of ["cu:exec", "shell:info", "term:spawn", "fs:watchSet", "git:inspect", "localrag:removeFile",
                 "remote-host-input", "remote:start", "local:write", "files:save",
                 "export:savePdf", "config:importFromFile", "conv:save", "hashmm:openTerminal",
                 "browser:embeddedNavigate", "browser:openExternal", "workspace-cache:get",
                 "workspace-cache:put", "workspace-cache:remove",
                 "project:pickSourceFolders", "project:activateSource",
                 "app:desktopPromptDecision", "app:desktopPromptReady", "app:closeChoice"]) {
  assert.strictEqual(G.isSensitiveChannel(c), true, `${c} 应判为敏感`);
}
ok("敏感通道识别（cu/shell/term/fs/git/localrag/remote/local:write/files:save/...）");

// 2. 非敏感通道
for (const c of ["notify:setEnabled", "memory:get", "app:ping", "theme:get"]) {
  assert.strictEqual(G.isSensitiveChannel(c), false, `${c} 不应判为敏感`);
}
ok("非敏感通道不误判");

// 3. 敏感通道 + 可信 sender（app 页 / 隐藏本地页 / 壳层）→ 允许
for (const url of [appPage, hiddenPage, shellPage]) {
  const d = G.decide("cu:exec", url, ctx);
  assert.strictEqual(d.allowed, true, `${url} 应允许`);
  assert.strictEqual(d.sensitive, true);
}
ok("敏感通道 + 可信 sender → 允许");

// 4. 敏感通道 + 不可信 sender（远程页）→ 拒绝
{
  const d = G.decide("shell:info", evilPage, ctx);
  assert.strictEqual(d.allowed, false, "远程 sender 调敏感通道应拒绝");
  assert.strictEqual(d.reason, "untrusted-sender");
}
ok("敏感通道 + 不可信远程 sender → 拒绝");

// 5. 非敏感通道即便 sender 不可信也放行（零行为变化）
{
  const d = G.decide("notify:setEnabled", evilPage, ctx);
  assert.strictEqual(d.allowed, true);
  assert.strictEqual(d.sensitive, false);
}
ok("非敏感通道不受影响");

// 6. V308 fail-closed：sender 无法判定时，敏感通道【拒绝】（原为放行，属 fail-open 漏洞）
{
  const d = G.decide("cu:exec", null, ctx);
  assert.strictEqual(d.allowed, false, "sender 未知的敏感通道必须拒绝（fail-closed）");
  assert.strictEqual(d.reason, "sender-unknown-denied");
}
ok("sender 未知 → 拒绝敏感通道（fail-closed，不再放行）");

// 6b. 未知 sender 的【非敏感】通道仍放行（不影响正常无害 IPC）
{
  const d = G.decide("app:getVersion", null, ctx);
  assert.strictEqual(d.allowed, true, "非敏感通道即便 sender 未知也放行");
}
ok("sender 未知 + 非敏感通道 → 放行");

// 7. 目录穿越/兄弟目录 file:// 不算可信
{
  const d = G.decide("term:spawn", "file:///opt/hashmm/desktop-evil/x.html", ctx);
  assert.strictEqual(d.allowed, false, "前缀相近的兄弟目录不可信");
}
ok("越界 file:// sender → 拒绝");

console.log(`\ntest_ipc-guard: ${pass} 项全部通过`);
