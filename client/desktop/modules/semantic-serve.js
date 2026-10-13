"use strict";
/** desktop/modules/semantic-serve.js — 本地语义嵌入服务编排（V98）。
 *
 *  职责：把主进程里已有的小模型能力（_sem_load / _sem_embed，V77 起内置）
 *  以 HTTP 形态喂给本地后端 sidecar：
 *    1. 开关持久化       config.semanticServe（默认关——铁律 3）
 *    2. provider 注册    向 shellserver 注册/注销 POST /local/embed 的处理函数
 *    3. env 计算         backend:start 时算出应注入的 HASHMM_LOCAL_EMBED_URL
 *                        （开关关 / 模型未就绪 / 壳层未起 → 空对象，后端零感知）
 *
 *  纯逻辑 + 依赖注入，不 require electron —— tests-node/test_semantic_serve.js
 *  在沙箱里直接冒烟。这是 V98 客户端模块化的样板：新能力进 modules/，
 *  main.js 只做装配。
 */

/**
 * @param {object} deps
 *   loadConfig () => cfg               桌面配置读取
 *   saveConfig (patch) => void         桌面配置写入
 *   semLoad    async () => boolean     惰性加载小模型（onnxruntime + 模型文件）
 *   semEmbed   async (text) => Float32Array|number[]   单条嵌入（L2 归一）
 *   getShell   () => shellSrv|null     惰性取壳层（启动时序解耦）
 *   log        (msg) => void           可选
 */
function createSemanticServe(deps) {
  const { loadConfig, saveConfig, semLoad, semEmbed, getShell } = deps;
  const log = deps.log || (() => {});

  function isOn() {
    try { return !!loadConfig().semanticServe; } catch (_) { return false; }
  }

  /** shellserver /local/embed 的 provider：texts[] → float[][]；未开/未就绪 → null（上游 503） */
  async function provider(texts) {
    if (!isOn()) return null;
    if (!(await semLoad())) return null;
    const out = [];
    for (const t of texts) {
      out.push(Array.from(await semEmbed(String(t == null ? "" : t).slice(0, 1200))));
    }
    return out;
  }

  /** 按当前开关向壳层注册/注销 provider（开关翻转、壳层就绪后都调一次） */
  function attach() {
    const s = getShell();
    if (s && typeof s.setEmbedProvider === "function") {
      s.setEmbedProvider(isOn() ? provider : null);
      log(`[semantic-serve] /local/embed provider ${isOn() ? "已注册" : "已注销"}`);
    }
  }

  function setOn(on) {
    try { saveConfig({ semanticServe: !!on }); } catch (e) {
      return { ok: false, error: "配置写入失败: " + (e && e.message) };
    }
    attach();
    return { ok: true, on: !!on, needRestart: true };   // env 注入需重启本地后端
  }

  /** backend:start 应注入的环境变量。任何前提不满足 → {}，后端行为零变化。 */
  async function envFor() {
    if (!isOn()) return {};
    const s = getShell();
    if (!s || !s.origin) return {};
    if (!(await semLoad())) {
      log("[semantic-serve] 模型未就绪，不注入 env（后端保持原生检索）");
      return {};
    }
    return { HASHMM_LOCAL_EMBED_URL: s.origin + "/local/embed" };
  }

  return { isOn, setOn, attach, envFor, provider };
}

module.exports = { createSemanticServe };
