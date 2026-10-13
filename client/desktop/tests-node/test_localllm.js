/** localllm 纯函数单测 —— 不触碰原生模块，沙箱直跑：node test_localllm.js */
"use strict";
const assert = require("assert");
const path = require("path");
const L = require("../localllm.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== resolveModelPath ===");
ok("拼出 models 下的 gguf 路径", L.resolveModelPath("/opt/app/models", "m.gguf") === path.join("/opt/app/models", "m.gguf"));
ok("缺文件名用默认", L.resolveModelPath("/x", "").endsWith(L.DEFAULT_LLM_FILE));

console.log("=== buildPrompt ===");
ok("无 context 原样返回问题", L.buildPrompt({ user: "你好" }) === "你好");
const p = L.buildPrompt({ context: "[1] 某文档片段", user: "营收多少？" });
ok("有 context 时并入片段", p.includes("[1] 某文档片段") && p.includes("营收多少？"));
ok("提示含防编造约束", p.includes("不要编造") || p.includes("依据不足"));

console.log("=== normalizeGenOptions ===");
const d = L.normalizeGenOptions({});
ok("默认 maxTokens=1024", d.maxTokens === 1024);
ok("默认 temperature=0.3", Math.abs(d.temperature - 0.3) < 1e-9);
ok("maxTokens 越界上裁剪", L.normalizeGenOptions({ maxTokens: 999999 }).maxTokens === 4096);
ok("maxTokens 越界下裁剪", L.normalizeGenOptions({ maxTokens: 1 }).maxTokens === 16);
ok("temperature 越界裁剪", L.normalizeGenOptions({ temperature: 9 }).temperature === 2);
ok("非法值回落默认", L.normalizeGenOptions({ temperature: "abc" }).temperature === 0.3);

console.log("=== localViability（复用 device_match）===");
// RTX 4090 24G：显存足 + 文件就绪 → 本地可行
const v1 = L.localViability({
  hw: { totalVramMB: 24576, hasGpu: true, cpuCount: 16, totalMemMB: 65536 },
  modelsDir: "/m", fileName: "x.gguf", existsFn: () => true,
});
ok("高显存+文件就绪→本地可行", v1.viable === true && v1.target === "local");
ok("识别为 high 档", v1.tier === "high");

// 显存足但文件缺失 → 不可行（回退云端），原因点明文件未就绪
const v2 = L.localViability({
  hw: { totalVramMB: 24576, hasGpu: true, cpuCount: 16, totalMemMB: 65536 },
  modelsDir: "/m", fileName: "x.gguf", existsFn: () => false,
});
ok("文件未就绪→不可行回退云端", v2.viable === false && v2.target === "cloud");
ok("原因点明文件未就绪", v2.reason.includes("未就绪"));

// 无独显且内存不足 → 回退云端
const v3 = L.localViability({
  hw: { totalVramMB: 0, hasGpu: false, cpuCount: 4, totalMemMB: 4096 },
  modelsDir: "/m", fileName: "x.gguf", existsFn: () => true,
});
ok("无独显+低内存→回退云端", v3.viable === false && v3.target === "cloud");

// 小显存（4G < 6G 门槛）→ 回退云端
const v4 = L.localViability({
  hw: { totalVramMB: 4096, hasGpu: true, cpuCount: 8, totalMemMB: 16384 },
  modelsDir: "/m", fileName: "x.gguf", existsFn: () => true,
});
ok("显存不足门槛→回退云端", v4.viable === false);

console.log("\n=== _isLoaded 初始为假（未加载原生模型）===");
ok("初始未加载", L._isLoaded() === false);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
