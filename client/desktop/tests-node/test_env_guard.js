/** test_env_guard.js — 环境隔离守卫单测（V101）。
 *  运行：node desktop/isolation/test_env_guard.js
 */
"use strict";
const assert = require("assert");
const G = require("../isolation/env-guard");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

const roots = G.allowedRoots({
  installDir: "C:\\Apps\\HashMM",
  userData: "C:\\Users\\me\\AppData\\Roaming\\HashMM",
  tmpDir: "C:\\Users\\me\\AppData\\Local\\Temp",
});

// 1. 包含判定：根内/根自身/前缀边界
{
  assert.strictEqual(G.isContained("C:\\Apps\\HashMM\\HashMM Files\\a.md", roots), true, "安装目录内");
  assert.strictEqual(G.isContained("C:\\Apps\\HashMM", roots), true, "根自身");
  assert.strictEqual(G.isContained("C:\\Users\\me\\AppData\\Roaming\\HashMM\\logs\\x.log", roots), true, "userData 内");
  assert.strictEqual(G.isContained("C:\\Users\\me\\AppData\\Local\\Temp\\hashmm-uninstall.bat", roots), true, "tmp 内");
  // 前缀边界：C:\Apps\HashMMOther 不应被 C:\Apps\HashMM 误配
  assert.strictEqual(G.isContained("C:\\Apps\\HashMMOther\\x", roots), false, "前缀边界不误配");
  // 大小写不敏感 + 斜杠归一
  assert.strictEqual(G.isContained("c:/apps/hashmm/sub/f.txt", roots), true, "大小写/斜杠归一");
}
ok("路径包含判定：根内/根自身/前缀边界/大小写斜杠归一");

// 2. 系统目录越界 → 不包含
{
  assert.strictEqual(G.isContained("C:\\Windows\\System32\\drivers\\etc\\hosts", roots), false, "系统目录越界");
  assert.strictEqual(G.isContained("C:\\Program Files\\Other\\x.dll", roots), false, "其它软件目录越界");
  assert.strictEqual(G.isContained("C:\\Users\\me\\Documents\\unrelated.txt", roots), false, "用户文档（非数据夹）越界");
  assert.strictEqual(G.isContained("", roots), false, "空路径");
}
ok("系统/他人目录越界判定：Windows/Program Files/无关文档/空");

// 3. assertContained 兜底：合规放行，越界抛
{
  assert.strictEqual(G.assertContained("C:\\Apps\\HashMM\\runtime\\python\\python.exe", roots), "C:\\Apps\\HashMM\\runtime\\python\\python.exe");
  assert.throws(() => G.assertContained("C:\\Windows\\x", roots), /隔离违规/, "越界写应抛");
}
ok("assertContained：合规放行 / 越界抛");

// 4. 批量审计
{
  const plan = [
    "C:\\Apps\\HashMM\\resources\\app.asar",
    "C:\\Apps\\HashMM\\HashMM Files\\Downloads\\a.zip",
    "C:\\Windows\\System32\\evil.dll", // 越界
  ];
  const r = G.auditPaths(plan, roots);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.violations.length, 1);
  assert.ok(r.violations[0].includes("System32"));
  // 全合规
  assert.strictEqual(G.auditPaths(plan.slice(0, 2), roots).ok, true);
}
ok("批量审计：挑出越界路径 / 全合规通过");

// 5. 策略清单存在且全 ok（文档/诊断用）
{
  const list = G.policyChecklist();
  assert.ok(list.length >= 5 && list.every((x) => x.ok), "隔离策略每条 ok");
  assert.ok(list.some((x) => /PATH/.test(x.rule)), "含不改 PATH 条款");
  assert.ok(list.some((x) => /Python/.test(x.rule)), "含不动系统 Python 条款");
  assert.ok(list.some((x) => /npm/.test(x.rule)), "含不全局 npm 条款");
}
ok("隔离策略清单：≥5 条且全 ok（不改 PATH/不动系统 Python/不全局 npm）");

console.log(`\ntest_env_guard: ${pass} 项全部通过`);
