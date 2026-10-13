/** test_ocr.js — 本地 OCR 流水线测试（V101）。
 *  纯算法：CTC 贪心解码、连通域取框、归一化、分行。
 *  真实推理：用 onnxruntime-node 实跑 test-fixtures/tiny.onnx（沙箱已验证可跑；
 *            onnxruntime-node 缺失时优雅跳过，不致失败）。
 *  运行：node desktop/models/test_ocr.js
 */
"use strict";
const assert = require("assert");
const path = require("path");
const O = require("../models/ocr-pipeline");
const RT = require("../models/onnx-runtime");

let pass = 0, skip = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };
const sk = (n) => { skip++; console.log("  ⊘ 跳过：" + n); };

// 1. CTC 贪心解码：argmax → 合并连续重复 → 去 blank → 映射字符集
{
  const charset = ["a", "b", "c"]; // 类别1→a, 2→b, 3→c；类别0=blank
  // 帧序列（每帧给目标类别最高分）：a a blank a b b → 解码应得 "aab"
  const hot = (k) => { const r = [0, 0, 0, 0]; r[k] = 1; return r; };
  const seq = [hot(1), hot(1), hot(0), hot(1), hot(2), hot(2)];
  assert.strictEqual(O.ctcGreedyDecode(seq, charset), "aab", "合并重复+去blank");
  // 全 blank → 空串
  assert.strictEqual(O.ctcGreedyDecode([hot(0), hot(0)], charset), "");
  // 连续不同字符不合并：a b c
  assert.strictEqual(O.ctcGreedyDecode([hot(1), hot(2), hot(3)], charset), "abc");
}
ok("CTC 贪心解码：合并连续重复 / 去 blank / 映射字符集");

// 2. argmax
assert.strictEqual(O.argmax([0.1, 0.7, 0.2]), 1);
assert.strictEqual(O.argmax([5, 1, 2]), 0);
ok("argmax");

// 3. 二值化 + 连通域取框
{
  // 5x5 图，两个分离的块：左上 2x2，右下 1x1（噪点，应被 minArea 过滤）
  const W = 5, H = 5;
  const prob = new Float32Array(W * H);
  const set = (x, y, v) => { prob[y * W + x] = v; };
  set(0, 0, 0.9); set(1, 0, 0.9); set(0, 1, 0.9); set(1, 1, 0.9); // 2x2 块=4 像素
  set(4, 4, 0.9); // 单像素噪点
  const bin = O.binarize(prob, 0.3);
  assert.strictEqual(bin[0], 1); assert.strictEqual(bin[12], 0);
  const boxes = O.extractBoxes(bin, W, H, 3); // minArea=3 → 过滤掉单像素
  assert.strictEqual(boxes.length, 1, "只剩 2x2 块（噪点被过滤）");
  assert.deepStrictEqual({ x: boxes[0].x, y: boxes[0].y, w: boxes[0].w, h: boxes[0].h }, { x: 0, y: 0, w: 2, h: 2 });
  // minArea=1 → 两个块都在
  assert.strictEqual(O.extractBoxes(bin, W, H, 1).length, 2);
}
ok("二值化 + 连通域取框（含面积过滤噪点）");

// 4. 归一化 CHW
{
  // 2x1 图，两个像素全白(255)与全黑(0)，mean=0.5 std=0.5
  const pixels = [255, 255, 255, 0, 0, 0];
  const t = O.normalizeToCHW(pixels, 2, 1, [0.5, 0.5, 0.5], [0.5, 0.5, 0.5]);
  // 白: (1-0.5)/0.5=1; 黑: (0-0.5)/0.5=-1
  assert.strictEqual(t.length, 6);
  assert.ok(Math.abs(t[0] - 1) < 1e-6, "通道0像素0=白→1");   // C0 plane, pixel0
  assert.ok(Math.abs(t[1] + 1) < 1e-6, "通道0像素1=黑→-1");  // C0 plane, pixel1
}
ok("归一化为 CHW（减均值除方差）");

// 5. 分行
{
  const boxes = [
    { x: 30, y: 0, w: 10, h: 8 }, { x: 0, y: 1, w: 10, h: 8 },  // 同一行（y 接近），乱序
    { x: 5, y: 30, w: 10, h: 8 },                                // 第二行
  ];
  const rows = O.groupByRows(boxes, 8);
  assert.strictEqual(rows.length, 2, "分成两行");
  assert.strictEqual(rows[0][0].x, 0, "第一行内按 x 排序");
  assert.strictEqual(rows[0][1].x, 30);
}
ok("检测框分行（同行按 x 排序）");

// 6. 真实 onnxruntime-node 推理（tiny.onnx）—— 证明运行时路径真能跑
(async () => {
  if (!RT.isAvailable()) { sk("onnxruntime-node 未安装（真机 npm install 后可跑）"); }
  else {
    const modelPath = path.join(__dirname, "..", "models", "test-fixtures", "tiny.onnx");
    const sess = await RT.loadSession(modelPath);
    assert.ok(sess, "应能加载 tiny.onnx 会话");
    const x = RT.makeTensor("float32", Float32Array.from([1, 2, 3]), [1, 3]);
    const out = await RT.run(sess, { X: x });
    assert.ok(out && out.Y, "应有输出 Y");
    const y = Array.from(out.Y.data);
    // tiny.onnx: Y = X·W + B, W=[[1,0],[0,1],[1,1]], B=[0.5,-0.5] → [4.5,4.5]
    assert.ok(Math.abs(y[0] - 4.5) < 1e-5 && Math.abs(y[1] - 4.5) < 1e-5, "推理结果正确");
    ok("真实 onnxruntime-node 推理 tiny.onnx → 结果数学正确（4.5,4.5）");
  }

  // 7. 运行时优雅降级：模型不存在 → 返回 null 不抛
  {
    const s = await RT.loadSession(path.join(__dirname, "nonexistent.onnx"));
    assert.strictEqual(s, null, "模型缺失返回 null");
    assert.strictEqual(await RT.run(null, {}), null, "空会话 run 返回 null");
  }
  ok("运行时优雅降级：模型缺失/空会话不抛");

  console.log(`\ntest_ocr: ${pass} 项通过${skip ? "，" + skip + " 项跳过" : ""}`);
})().catch((e) => { console.error("失败：", e); process.exit(1); });
