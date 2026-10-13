/** test_llm-preflight.js — LLM 连通性预检纯逻辑单测。 */
"use strict";
const assert = require("assert");
const P = require("../llm-preflight.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== parseModelsResponse 多形态 ===");
ok("OpenAI {data:[{id}]}", JSON.stringify(P.parseModelsResponse({ data: [{ id: "gpt-4" }, { id: "gpt-3.5" }] })) === JSON.stringify(["gpt-4", "gpt-3.5"]));
ok("裸数组 [{id}]", P.parseModelsResponse([{ id: "m1" }]).length === 1);
ok("{models:[{name}]}", P.parseModelsResponse({ models: [{ name: "qwen" }] })[0] === "qwen");
ok("字符串数组", P.parseModelsResponse(["a", "b"]).length === 2);
ok("{data:[字符串]}", P.parseModelsResponse({ data: ["x"] })[0] === "x");
ok("去重", P.parseModelsResponse({ data: [{ id: "m" }, { id: "m" }] }).length === 1);
ok("空/非法→空数组", P.parseModelsResponse(null).length === 0 && P.parseModelsResponse({ foo: 1 }).length === 0);

console.log("=== classifyHttpStatus ===");
ok("401→认证失败", P.classifyHttpStatus(401).level === "error" && P.classifyHttpStatus(401).message.includes("认证"));
ok("404→端点不存在", P.classifyHttpStatus(404).message.includes("端点"));
ok("429→限流(warn)", P.classifyHttpStatus(429).level === "warn");
ok("500→服务端错误", P.classifyHttpStatus(503).level === "error");
ok("200→ok 无消息", P.classifyHttpStatus(200).level === "ok");

console.log("=== classifyNetKind ===");
ok("refused", P.classifyNetKind("refused").message.includes("监听"));
ok("timeout", P.classifyNetKind("timeout").message.includes("超时"));
ok("ENOTFOUND", P.classifyNetKind("", "ENOTFOUND").message.includes("解析"));
ok("证书", P.classifyNetKind("", "SELF_SIGNED_CERT").message.includes("证书"));

console.log("=== buildVerdict 综合结论 ===");
const unreach = P.buildVerdict({ reachable: false, netKind: "refused" });
ok("不可达→error", unreach.level === "error" && unreach.ok === false);
const e401 = P.buildVerdict({ reachable: true, status: 401 });
ok("401→error 不ok", e401.ok === false && e401.message.includes("认证"));
const good = P.buildVerdict({ reachable: true, status: 200, models: ["deepseek-chat", "deepseek-reasoner"], wantedModel: "deepseek-chat" });
ok("连通+模型在→ok level=ok", good.ok === true && good.level === "ok" && good.modelFound === true);
const wrongModel = P.buildVerdict({ reachable: true, status: 200, models: ["a", "b"], wantedModel: "not-there" });
ok("连通但模型不在→warn", wrongModel.ok === true && wrongModel.level === "warn" && wrongModel.modelFound === false);
ok("warn 仍返回模型清单", wrongModel.models.length === 2);
const noModelGiven = P.buildVerdict({ reachable: true, status: 200, models: ["a", "b", "c"] });
ok("没填模型→报发现N个", noModelGiven.level === "ok" && noModelGiven.message.includes("3"));
const parseFail = P.buildVerdict({ reachable: true, status: 200, parseFailed: true });
ok("连通但列不出模型→warn", parseFail.ok === true && parseFail.level === "warn");
ok("空models且非parseFail→warn", P.buildVerdict({ reachable: true, status: 200, models: [] }).level === "warn");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
