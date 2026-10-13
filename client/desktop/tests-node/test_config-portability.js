/** test_config-portability.js — 配置导入导出纯逻辑单测。 */
"use strict";
const assert = require("assert");
const C = require("../config-portability.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

const parts = {
  mainConfig: { url: "http://a.com:6006", token: "tok-aaa", recent: [{ url: "http://a.com:6006", token: "tok-aaa" }, { url: "http://b.com:20014", token: "tok-bbb" }] },
  llmCfg: { base: "https://api.deepseek.com", model: "deepseek-chat", key: "sk-secret" },
  visionCfg: { base: "https://vis.com", model: "gpt-vision", key: "vk-secret" },
  preferences: { theme: "dark", fontSize: 14 },
};

console.log("=== buildExport 含密钥 ===");
const exp = C.buildExport(parts, { includeSecrets: true });
ok("kind 标记", exp.kind === "hashmm-config-export");
ok("版本", exp.version === C.EXPORT_VERSION);
ok("secretsIncluded=true", exp.secretsIncluded === true);
ok("后端去重(2个)", exp.backends.length === 2);
ok("后端含 token", exp.backends[0].token === "tok-aaa");
ok("llm key 保留", exp.llm.key === "sk-secret");
ok("vision key 保留", exp.vision.key === "vk-secret");
ok("preferences 透传", exp.preferences.theme === "dark");

console.log("=== buildExport 脱敏 ===");
const red = C.buildExport(parts, { includeSecrets: false });
ok("secretsIncluded=false", red.secretsIncluded === false);
ok("后端不含 token", red.backends[0].token === undefined);
ok("llm key 抹掉", red.llm.key === "");
ok("vision key 抹掉", red.vision.key === "");
ok("但 base/model 保留", red.llm.base === "https://api.deepseek.com" && red.llm.model === "deepseek-chat");

console.log("=== stringifyExport ===");
ok("产出 JSON 字符串", typeof C.stringifyExport(parts, { includeSecrets: true }) === "string");
ok("可被解析回去", JSON.parse(C.stringifyExport(parts, {})).kind === "hashmm-config-export");

console.log("=== parseImport 校验 ===");
ok("坏 JSON→拒", C.parseImport("{不是json").ok === false);
ok("空→拒", C.parseImport(null).ok === false);
ok("非本应用→拒", C.parseImport({ kind: "other" }).ok === false);
ok("高版本→拒", C.parseImport({ kind: "hashmm-config-export", version: 999 }).ok === false);
const imp = C.parseImport(C.stringifyExport(parts, { includeSecrets: true }));
ok("合法→ok", imp.ok === true);
ok("后端解析", imp.data.backends.length === 2 && imp.data.backends[0].url === "http://a.com:6006");
ok("llm 解析", imp.data.llm.key === "sk-secret");
const redImp = C.parseImport(C.buildExport(parts, { includeSecrets: false }));
ok("脱敏导入有警告", redImp.warnings.some(w => w.includes("脱敏")));
const noBackend = C.parseImport({ kind: "hashmm-config-export", version: 1, backends: [] });
ok("无后端有警告", noBackend.warnings.some(w => w.includes("没有后端")));

console.log("=== splitForApply 拆分 ===");
const split = C.splitForApply(imp.data);
ok("首个后端作当前", split.mainPatch.url === "http://a.com:6006");
ok("recent 保留全部", split.mainPatch.recent.length === 2);
ok("recent 带 ts", typeof split.mainPatch.recent[0].ts === "number");
ok("llmCfg 拆出", split.llmCfg.model === "deepseek-chat");
ok("visionCfg 拆出", split.visionCfg.base === "https://vis.com");
ok("preferences 拆出", split.preferences.theme === "dark");
ok("空数据不崩", typeof C.splitForApply(null).mainPatch === "object");

console.log("=== 往返一致性 ===");
const round = C.splitForApply(C.parseImport(C.stringifyExport(parts, { includeSecrets: true })).data);
ok("往返后 url 一致", round.mainPatch.url === "http://a.com:6006");
ok("往返后 token 一致", round.mainPatch.token === "tok-aaa");
ok("往返后 llm key 一致", round.llmCfg.key === "sk-secret");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
