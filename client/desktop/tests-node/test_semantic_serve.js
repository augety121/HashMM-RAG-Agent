/** test_semantic_serve.js — V98 本地语义服务编排模块冒烟（纯逻辑 DI，沙箱可跑）。
 *  覆盖：默认关、开关持久化 + 即时注册/注销、provider 三重门控
 *  （关=null / 模型未就绪=null / 就绪=向量+截断）、envFor 四态
 *  （关 / 壳层缺位 / 模型未就绪 / 全就绪=注入 URL）。 */
"use strict";
const assert = require("assert");
const { createSemanticServe } = require("../modules/semantic-serve");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

(async () => {
  // ── 假依赖 ──
  let cfg = {};
  let modelReady = false;
  let registered = "untouched";   // 记录 setEmbedProvider 收到什么
  const shell = {
    origin: "http://127.0.0.1:17615",
    setEmbedProvider(fn) { registered = fn; },
  };
  let shellRef = shell;
  const seen = [];
  const S = createSemanticServe({
    loadConfig: () => cfg,
    saveConfig: (patch) => { cfg = Object.assign({}, cfg, patch); },
    semLoad: async () => modelReady,
    semEmbed: async (t) => { seen.push(t); return new Float32Array([t.length, 0.5]); },
    getShell: () => shellRef,
    log: () => {},
  });

  // 1. 默认关
  assert.strictEqual(S.isOn(), false, "semanticServe 默认必须为关");
  assert.deepStrictEqual(await S.envFor(), {}, "默认关不得注入 env");
  assert.strictEqual(await S.provider(["x"]), null, "默认关 provider 必须 null（上游 503）");
  ok("默认关：isOn=false / env={} / provider=null");

  // 2. 开关持久化 + attach 即时注册
  let r = S.setOn(true);
  assert.deepStrictEqual({ ok: r.ok, on: r.on, needRestart: r.needRestart }, { ok: true, on: true, needRestart: true });
  assert.strictEqual(cfg.semanticServe, true, "开关应写入配置");
  assert.strictEqual(typeof registered, "function", "开 → 应向壳层注册 provider 函数");
  ok("setOn(true)：写配置 + 注册 provider + 提示重启");

  // 3. 开了但模型未就绪 → provider null、envFor {}
  modelReady = false;
  assert.strictEqual(await S.provider(["x"]), null);
  assert.deepStrictEqual(await S.envFor(), {}, "模型未就绪不得注入 env");
  ok("开但模型未就绪：provider=null / env={}（后端零感知）");

  // 4. 全就绪 → provider 出向量（含 null 元素防御 + 1200 字截断），envFor 注入 URL
  modelReady = true;
  seen.length = 0;
  const vecs = await S.provider(["abc", null, "x".repeat(5000)]);
  assert.strictEqual(vecs.length, 3);
  assert.deepStrictEqual(vecs[0], [3, 0.5], "Float32Array 应转普通数组");
  assert.strictEqual(seen[1], "", "null 元素应转空串");
  assert.strictEqual(seen[2].length, 1200, "超长文本应截到 1200 字");
  assert.deepStrictEqual(await S.envFor(),
    { HASHMM_LOCAL_EMBED_URL: "http://127.0.0.1:17615/local/embed" });
  ok("全就绪：向量正确（截断/判空）+ env 注入壳层 /local/embed");

  // 5. 壳层缺位 → envFor {}（attach 也不炸）
  shellRef = null;
  assert.deepStrictEqual(await S.envFor(), {}, "壳层未起不得注入 env");
  S.attach();   // 不应抛
  shellRef = shell;
  ok("壳层缺位：env={} 且 attach 安静跳过");

  // 6. 关回去 → 注销 provider、env 清空
  r = S.setOn(false);
  assert.strictEqual(r.on, false);
  assert.strictEqual(registered, null, "关 → 应向壳层注销（传 null）");
  assert.deepStrictEqual(await S.envFor(), {});
  assert.strictEqual(await S.provider(["x"]), null);
  ok("setOn(false)：注销 provider + env 清空");

  console.log(`\ntest_semantic_serve: ${pass} 项全部通过`);
})().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
