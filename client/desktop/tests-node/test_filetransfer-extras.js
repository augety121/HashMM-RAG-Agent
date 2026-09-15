/** test_filetransfer-extras.js — 文件传输扩展纯逻辑单测。 */
"use strict";
const assert = require("assert");
const F = require("../filetransfer-extras.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== missingChunks ===");
ok("全收→无缺", F.missingChunks([0, 1, 2, 3], 4).length === 0);
ok("缺中间", JSON.stringify(F.missingChunks([0, 2, 3], 4)) === JSON.stringify([1]));
ok("缺多块", JSON.stringify(F.missingChunks([1], 4)) === JSON.stringify([0, 2, 3]));
ok("全缺", JSON.stringify(F.missingChunks([], 3)) === JSON.stringify([0, 1, 2]));
ok("乱序收也对", JSON.stringify(F.missingChunks([3, 1, 0], 4)) === JSON.stringify([2]));
ok("空/0 不崩", F.missingChunks(null, 0).length === 0);

console.log("=== resumePlan ===");
const rp = F.resumePlan([0, 1], 4);
ok("缺块数", rp.missingCount === 2);
ok("已收数", rp.receivedCount === 2);
ok("百分比", rp.pct === 50);
ok("未完成", rp.complete === false);
ok("全收→complete", F.resumePlan([0, 1, 2, 3], 4).complete === true);
ok("空总数→不complete", F.resumePlan([], 0).complete === false);

console.log("=== toRanges 连续区间合并 ===");
ok("连续合并", JSON.stringify(F.toRanges([0, 1, 2, 5, 6])) === JSON.stringify([[0, 2], [5, 6]]));
ok("单块", JSON.stringify(F.toRanges([3])) === JSON.stringify([[3, 3]]));
ok("全连续", JSON.stringify(F.toRanges([0, 1, 2])) === JSON.stringify([[0, 2]]));
ok("乱序去重", JSON.stringify(F.toRanges([2, 0, 1, 1])) === JSON.stringify([[0, 2]]));
ok("空→空", F.toRanges([]).length === 0);

console.log("=== formatBytes ===");
ok("B", F.formatBytes(500) === "500 B");
ok("KB", F.formatBytes(2048) === "2.0 KB");
ok("MB", F.formatBytes(5 * 1024 * 1024) === "5.0 MB");
ok("GB", F.formatBytes(2 * 1024 * 1024 * 1024) === "2.00 GB");

console.log("=== formatEta ===");
ok("秒", F.formatEta(5000) === "5 秒");
ok("分秒", F.formatEta(90000).includes("分"));
ok("时分", F.formatEta(3700000).includes("时"));
ok("负/无穷→—", F.formatEta(-1) === "—");

console.log("=== transferStats 速率+ETA ===");
const st = F.transferStats(1024 * 1024, 4 * 1024 * 1024, 1000);  // 1MB/1s → 1MB/s，剩3MB→3s
ok("百分比 25%", st.pct === 25);
ok("速率约 1MB/s", st.speedLabel.includes("MB/s"));
ok("ETA 约 3 秒", st.etaLabel.includes("秒"));
ok("doneLabel", st.doneLabel.includes("MB"));
ok("totalLabel", st.totalLabel.includes("MB"));
const st0 = F.transferStats(0, 1000, 0);
ok("0耗时不崩(ETA—)", st0.etaLabel === "—");
ok("已完成 100%", F.transferStats(1000, 1000, 500).pct === 100);
ok("空总数→0%", F.transferStats(0, 0, 100).pct === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
