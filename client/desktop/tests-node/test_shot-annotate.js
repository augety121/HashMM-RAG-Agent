/** test_shot-annotate.js — 截图标注几何纯逻辑单测。 */
"use strict";
const assert = require("assert");
const A = require("../shot-annotate.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== normalizeRect 任意方向 ===");
ok("左上→右下", JSON.stringify(A.normalizeRect(10, 10, 50, 40)) === JSON.stringify({ x: 10, y: 10, w: 40, h: 30 }));
ok("右下→左上", JSON.stringify(A.normalizeRect(50, 40, 10, 10)) === JSON.stringify({ x: 10, y: 10, w: 40, h: 30 }));
ok("右上→左下", JSON.stringify(A.normalizeRect(50, 10, 10, 40)) === JSON.stringify({ x: 10, y: 10, w: 40, h: 30 }));
ok("零尺寸", JSON.stringify(A.normalizeRect(5, 5, 5, 5)) === JSON.stringify({ x: 5, y: 5, w: 0, h: 0 }));

console.log("=== scalePoint 显示→原始 ===");
const p = A.scalePoint(100, 50, 400, 200, 1600, 800);  // 4x 放大
ok("x 缩放4倍", p.x === 400);
ok("y 缩放4倍", p.y === 200);
ok("1:1 不变", JSON.stringify(A.scalePoint(10, 20, 100, 100, 100, 100)) === JSON.stringify({ x: 10, y: 20 }));
ok("displayW=0 不除零", typeof A.scalePoint(10, 10, 0, 100, 100, 100).x === "number");

console.log("=== scaleRect ===");
const r = A.scaleRect({ x: 10, y: 5, w: 20, h: 10 }, 200, 100, 800, 400);  // 4x
ok("rect 整体缩放", JSON.stringify(r) === JSON.stringify({ x: 40, y: 20, w: 80, h: 40 }));
ok("空 rect 不崩", typeof A.scaleRect(null, 100, 100, 100, 100).x === "number");

console.log("=== isTinyRect ===");
ok("过小→true", A.isTinyRect({ w: 2, h: 2 }) === true);
ok("够大→false", A.isTinyRect({ w: 20, h: 20 }) === false);
ok("一边过小→true", A.isTinyRect({ w: 100, h: 1 }) === true);
ok("自定义阈值", A.isTinyRect({ w: 10, h: 10 }, 20) === true);

console.log("=== clampPoint ===");
ok("越界收回上界", JSON.stringify(A.clampPoint(150, 150, 100, 100)) === JSON.stringify({ x: 100, y: 100 }));
ok("越界收回下界", JSON.stringify(A.clampPoint(-5, -5, 100, 100)) === JSON.stringify({ x: 0, y: 0 }));
ok("范围内不变", JSON.stringify(A.clampPoint(50, 60, 100, 100)) === JSON.stringify({ x: 50, y: 60 }));

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
