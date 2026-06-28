/** test_cu_grounding.js — Computer Use 视觉元素定位单测（V100）。
 *  纯逻辑：喂合成 OCR 文本框 + 目标文字，断言定位到正确元素、归一化坐标正确、
 *  分层打分（精确/前缀/包含/词重叠/模糊）、阈值拒绝、歧义提示。
 *  关键串联：定位结果 → cu-actions.validateAction → applyPolicy，证明"定位出的
 *  点击"同样受坐标禁区/安全策略约束（不是绕过守卫的后门）。
 *  运行：node desktop/tests-node/test_cu_grounding.js
 */
"use strict";
const assert = require("assert");
const G = require("../modules/cu-grounding");
const A = require("../modules/cu-actions");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

const SCREEN = { width: 1000, height: 1000 }; // 用 1000×1000 让像素≈归一化，便于断言

// 一块典型登录界面的 OCR 结果
const OCR = [
  { text: "用户名", x1: 100, y1: 100, x2: 200, y2: 130 },
  { text: "密码", x1: 100, y1: 160, x2: 180, y2: 190 },
  { text: "登录", x1: 400, y1: 500, x2: 520, y2: 560 },     // 中心 (460,530)
  { text: "取消", x1: 560, y1: 500, x2: 680, y2: 560 },
  { text: "忘记密码？", x1: 400, y1: 580, x2: 560, y2: 610 },
];

// 1. 精确匹配 + 归一化中心正确
{
  const r = G.locateElement(OCR, "登录", SCREEN);
  assert.ok(r.found, "应找到「登录」");
  assert.strictEqual(r.how, "exact");
  assert.strictEqual(r.nx, 460, `登录中心 nx 应 460，实际 ${r.nx}`);
  assert.strictEqual(r.ny, 530, `登录中心 ny 应 530，实际 ${r.ny}`);
}
ok("精确匹配定位 + 归一化中心坐标正确");

// 2. 前缀/包含：query 是元素子串
{
  const r = G.scoreMatch("密码", "忘记密码？");
  assert.ok(r.score >= 0.7 && r.how === "contains", `应判包含，实际 ${JSON.stringify(r)}`);
  // 但"密码"精确命中独立的"密码"元素应排在"忘记密码？"前
  const loc = G.locateElement(OCR, "密码", SCREEN);
  assert.strictEqual(loc.text, "密码", "精确的「密码」应胜过「忘记密码？」");
  assert.strictEqual(loc.how, "exact");
}
ok("子串包含打分 + 精确优先于包含");

// 3. 模糊匹配：OCR 把"Submit"误读成"Submil"（t→l，真实 OCR 噪声）
{
  const noisy = [{ text: "Submil", x1: 400, y1: 500, x2: 520, y2: 560 }];
  const r = G.locateElement(noisy, "Submit", SCREEN, { minScore: 0.4 });
  assert.ok(r.found, "近似词应能在较低阈值下匹配");
  assert.ok(["fuzzy", "tokens", "contains", "prefix"].includes(r.how), `应走近似类匹配，实际 ${r.how}`);
  // 而真正不相似的（共享字符过少）应被拒，避免误点
  const wrong = G.scoreMatch("登录", "登陆");
  assert.ok(wrong.score < 0.55, "仅共享一半字符的词不应达到可点阈值（防误点）");
}
ok("模糊匹配（OCR 误读 Submit/Submil 命中；半相似词被拒，防误点）");

// 4. 阈值拒绝：完全不相关的目标不乱点
{
  const r = G.locateElement(OCR, "提交订单并支付", SCREEN);
  assert.strictEqual(r.found, false, "无相关元素应判未找到");
  assert.ok(Array.isArray(r.alternatives), "应回传备选供上层参考");
  assert.ok(r.reason, "应给出未找到原因");
}
ok("阈值拒绝（不相关目标不乱点）+ 回传备选与原因");

// 5. 歧义提示：两个同样可信的候选
{
  const dup = [
    { text: "确定", x1: 100, y1: 100, x2: 200, y2: 140 },
    { text: "确定", x1: 600, y1: 100, x2: 700, y2: 140 },
  ];
  const r = G.locateElement(dup, "确定", SCREEN);
  assert.ok(r.found && r.ambiguous, "两个相同文字应标记 ambiguous，交上层确认");
}
ok("歧义检测（多个等可信候选 → ambiguous）");

// 6. 文本规范化：大小写/标点/空白无关
{
  assert.strictEqual(G.normalizeLabel("  Submit！ "), "submit");
  assert.strictEqual(G.normalizeLabel("【保存】"), "保存");
  const r = G.scoreMatch("submit", "Submit");
  assert.strictEqual(r.score, 1, "大小写不同应仍精确匹配");
}
ok("文本规范化（大小写/中英标点/空白无关）");

// 7. 排序：rankElements 按分降序、过滤零分
{
  const ranked = G.rankElements(OCR, "登录", SCREEN);
  assert.ok(ranked.length >= 1 && ranked[0].text === "登录");
  assert.ok(ranked.every((r) => r.score > 0), "应过滤零分候选");
  for (let i = 1; i < ranked.length; i++) assert.ok(ranked[i - 1].score >= ranked[i].score, "应降序");
}
ok("候选排序（降序 + 过滤零分）");

// 8. ★串联：定位结果喂进 cu-actions 流水线，禁区策略照常生效（非后门）
{
  // 把"登录"定位成一次 left_click，过 validateAction（用归一化 x/y）
  const loc = G.locateElement(OCR, "登录", SCREEN);
  const v = A.validateAction({ type: "left_click", x: loc.nx, y: loc.ny }, SCREEN);
  assert.ok(v.ok, "定位出的点击应是合法动作");
  const plan = v.plan;
  assert.strictEqual(plan.nx, 460);
  // 普通区域：默认策略不需确认
  const p1 = A.applyPolicy(plan, {});
  assert.ok(p1.allow && !p1.needsConfirm, "普通区域点击默认放行");

  // 若目标落在自定义禁区，定位出的点击也必须被拦成"需确认"——证明定位不绕过守卫
  const zone = [{ name: "测试禁区", x1: 400, y1: 500, x2: 500, y2: 560 }];
  const p2 = A.applyPolicy(plan, { noClickZones: zone });
  assert.ok(p2.allow && p2.needsConfirm, "定位出的点击落入禁区也应触发确认（受同一守卫约束）");
  assert.ok(/禁区|敏感/.test(p2.reason), "应说明因禁区需确认");

  // 只读模式：定位出的点击同样被禁
  const p3 = A.applyPolicy(plan, { blockWrites: true });
  assert.strictEqual(p3.allow, false, "只读模式下定位出的点击应被禁止");
}
ok("串联 cu-actions：定位点击受 validateAction/禁区/只读策略同等约束（非后门）");

console.log(`\ntest_cu_grounding: ${pass} 项全部通过`);
