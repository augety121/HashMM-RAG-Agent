/** test_client-validate.js — 客户端输入校验/错误归类单测（纯逻辑）。 */
"use strict";
const assert = require("assert");
const V = require("../client-validate.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== validateBackendUrl ===");
ok("空→拒", V.validateBackendUrl("").ok === false);
ok("无协议自动补 http", V.validateBackendUrl("127.0.0.1:6006").normalized === "http://127.0.0.1:6006");
ok("https 保留", V.validateBackendUrl("https://api.x.com").normalized === "https://api.x.com");
ok("去末尾斜杠", V.validateBackendUrl("http://a.com/").normalized === "http://a.com");
ok("保留路径", V.validateBackendUrl("http://a.com/api/").normalized === "http://a.com/api");
ok("非法地址→拒", V.validateBackendUrl("ht!tp://%%%").ok === false);
ok("非法端口→拒", V.validateBackendUrl("http://a.com:99999").ok === false);

console.log("=== validateApiKey ===");
ok("必填且空→拒", V.validateApiKey("", { required: true }).ok === false);
ok("非必填空→放行", V.validateApiKey("", { required: false }).ok === true);
ok("含空格→拒", V.validateApiKey("ab cd ef gh", { required: true }).ok === false);
ok("太短→拒", V.validateApiKey("abc", { required: true }).ok === false);
ok("正常→过", V.validateApiKey("sk-1234567890abcdef", { required: true }).ok === true);

console.log("=== validateModelName ===");
ok("空→拒(默认必填)", V.validateModelName("").ok === false);
ok("非必填空→过", V.validateModelName("", { required: false }).ok === true);
ok("正常→过", V.validateModelName("deepseek-chat").ok === true);
ok("过长→拒", V.validateModelName("x".repeat(200)).ok === false);

console.log("=== validateLlmConfig 聚合 ===");
const good = V.validateLlmConfig({ base: "127.0.0.1:6006", key: "sk-abcdefgh", model: "deepseek-chat" }, { keyRequired: true });
ok("全合法→ok", good.ok === true && good.normalized.base === "http://127.0.0.1:6006");
const bad = V.validateLlmConfig({ base: "", key: "x", model: "" }, { keyRequired: true });
ok("多错→ok=false", bad.ok === false);
ok("逐字段报错", bad.errors.base && bad.errors.key && bad.errors.model);
const noKey = V.validateLlmConfig({ base: "http://localhost:1", key: "", model: "m" }, { keyRequired: false });
ok("key 非必填→放行", noKey.ok === true);

console.log("=== validateFolderPath ===");
ok("空→拒", V.validateFolderPath("").ok === false);
ok("Windows 路径→过", V.validateFolderPath("C:\\docs").ok === true);
ok("nix 绝对路径→过", V.validateFolderPath("/home/user/docs").ok === true);
ok("UNC→过", V.validateFolderPath("\\\\server\\share").ok === true);
ok("相对路径→拒", V.validateFolderPath("docs/sub").ok === false);

console.log("=== classifyConnError 归类 ===");
ok("401→认证失败", V.classifyConnError({ status: 401 }).kind === "auth");
ok("ECONNREFUSED→后端没启动", V.classifyConnError({ code: "ECONNREFUSED" }).kind === "refused");
ok("超时", V.classifyConnError({ code: "ETIMEDOUT" }).kind === "timeout");
ok("DNS", V.classifyConnError({ code: "ENOTFOUND" }).kind === "dns");
ok("证书", V.classifyConnError(new Error("self signed certificate")).kind === "tls");
ok("500→server", V.classifyConnError({ status: 503 }).kind === "server");
ok("404→notfound", V.classifyConnError({ status: 404 }).kind === "notfound");
ok("未知→unknown 但有提示", (() => { const r = V.classifyConnError("怪错误"); return r.kind === "unknown" && !!r.hint; })());
ok("每种都带人话 message+hint", ["auth", "refused", "timeout"].every(k => {
  const r = V.classifyConnError(k === "auth" ? { status: 401 } : { code: k === "refused" ? "ECONNREFUSED" : "ETIMEDOUT" });
  return r.message && r.hint;
}));

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
