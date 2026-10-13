/** test_image_ops.js — 纯 JS 图像操作单测（V101）。
 *  运行：node desktop/models/test_image_ops.js
 */
"use strict";
const assert = require("assert");
const IMG = require("../models/image-ops");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 构造 RGB 像素的小工具
function mk(w, h, fn) {
  const px = new Uint8Array(w * h * 3);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const [r, g, b] = fn(x, y); const i = (y * w + x) * 3;
    px[i] = r; px[i + 1] = g; px[i + 2] = b;
  }
  return px;
}

// 1. 裁剪
{
  // 4x4，每格灰度=x*10+y，裁 (1,1,2x2)
  const W = 4, H = 4;
  const px = mk(W, H, (x, y) => { const v = x * 10 + y; return [v, v, v]; });
  const c = IMG.cropBox(px, W, H, { x: 1, y: 1, w: 2, h: 2 });
  assert.strictEqual(c.width, 2); assert.strictEqual(c.height, 2);
  // 左上应是 (x=1,y=1)=11
  assert.strictEqual(c.pixels[0], 11);
  assert.strictEqual(c.pixels[(0 * 2 + 1) * 3], 21, "右上=（x=2,y=1)=21");
  // 越界夹取
  const c2 = IMG.cropBox(px, W, H, { x: 3, y: 3, w: 5, h: 5 });
  assert.strictEqual(c2.width, 1); assert.strictEqual(c2.height, 1);
}
ok("裁剪框：尺寸/取值/越界夹取");

// 2. 双线性缩放：纯色保持；尺寸正确
{
  const W = 2, H = 2;
  const red = mk(W, H, () => [200, 50, 10]);
  const r = IMG.resizeBilinear(red, W, H, 5, 3);
  assert.strictEqual(r.width, 5); assert.strictEqual(r.height, 3);
  // 纯色：所有像素仍是该色（双线性插值同色不变）
  for (let i = 0; i < r.width * r.height; i++) {
    assert.strictEqual(r.pixels[i * 3], 200);
    assert.strictEqual(r.pixels[i * 3 + 1], 50);
    assert.strictEqual(r.pixels[i * 3 + 2], 10);
  }
  // 渐变缩放：单调性（左黑右白，放大后第一像素 < 最后像素）
  const grad = mk(2, 1, (x) => { const v = x * 255; return [v, v, v]; });
  const g = IMG.resizeBilinear(grad, 2, 1, 6, 1);
  assert.ok(g.pixels[0] < g.pixels[(5) * 3], "渐变放大保持左<右");
  // 空尺寸安全
  assert.strictEqual(IMG.resizeBilinear(red, W, H, 0, 0).pixels.length, 0);
}
ok("双线性缩放：纯色不变/尺寸正确/渐变单调/空尺寸安全");

// 3. 灰度
{
  const px = mk(1, 1, () => [255, 0, 0]); // 纯红
  const g = IMG.toGrayscale(px, 1, 1);
  assert.strictEqual(g.length, 1);
  assert.strictEqual(g[0], Math.round(0.299 * 255), "红的灰度=0.299*255≈76");
  const white = IMG.toGrayscale(mk(1, 1, () => [255, 255, 255]), 1, 1);
  assert.strictEqual(white[0], 255, "白→255");
}
ok("灰度：Rec.601 加权（红≈76，白=255）");

// 4. CRNN 预处理：等高 32、归一化到 [-1,1]、单通道长度正确
{
  const W = 20, H = 10;
  const px = mk(W, H, () => [128, 128, 128]); // 中灰
  const pre = IMG.preprocessForCrnn(px, W, H, { x: 0, y: 0, w: 20, h: 10 }, 32);
  assert.strictEqual(pre.height, 32, "等高 32");
  // 等比：原 20x10，ratio=2 → 宽 = 32*2 = 64
  assert.strictEqual(pre.width, 64, "等比宽度");
  assert.strictEqual(pre.data.length, 32 * 64, "单通道 H*W");
  // 中灰 128/255≈0.502 → (0.502-0.5)/0.5≈0.004，接近 0
  assert.ok(Math.abs(pre.data[0]) < 0.02, "中灰归一化≈0");
  // 全白 → 接近 +1；全黑 → 接近 -1
  const wpre = IMG.preprocessForCrnn(mk(W, H, () => [255, 255, 255]), W, H, { x: 0, y: 0, w: 20, h: 10 }, 32);
  assert.ok(Math.abs(wpre.data[0] - 1) < 1e-6, "白→+1");
  const bpre = IMG.preprocessForCrnn(mk(W, H, () => [0, 0, 0]), W, H, { x: 0, y: 0, w: 20, h: 10 }, 32);
  assert.ok(Math.abs(bpre.data[0] + 1) < 1e-6, "黑→-1");
}
ok("CRNN 预处理：等高32/等比宽/单通道/归一化[-1,1]");

console.log(`\ntest_image_ops: ${pass} 项全部通过`);
