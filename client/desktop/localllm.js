/**
 * desktop/localllm.js — 本地 LLM 推理（V103.90，方案3：数据全程不出端的企业级私有模式）。
 *
 * 背景：桌面端此前"本地"只到检索（localrag 的 BM25）与嵌入（semantic 的 ONNX），**答案生成仍走
 * 云端 deepseek**。本模块补上最后一环——用用户自训的 7B（导成 GGUF Q4_K_M）在本机生成答案，
 * 配合本地知识库 + 本地嵌入，凑齐"检索→嵌入→生成"全链路离线，文档与问答数据一字不出设备。
 * 这是大厂云服务给不了的差异化（数据主权/合规/内网隔离）。
 *
 * 运行时用 node-llama-cpp（llama.cpp 的 Node 绑定，Electron 友好）。它是 ESM-only，而本工程是
 * CommonJS，故用**动态 import**（await import(...)）按需加载——装了才用，没装自动回退云端，绝不
 * 因为缺这个可选原生依赖而让应用起不来。真正的 GGUF 加载/推理需原生模块 + 模型文件（真机 RTX
 * 4090），沙箱只验证纯逻辑（路径解析/提示拼装/参数裁剪/本地可行性决策）与语法（node --check）。
 *
 * API 事实（已核实 node-llama-cpp v3）：
 *   const llama = await getLlama();
 *   const model = await llama.loadModel({ modelPath });
 *   const context = await model.createContext();
 *   const session = new LlamaChatSession({ contextSequence: context.getSequence(), systemPrompt });
 *   const text = await session.prompt(userText, { onTextChunk });   // 流式回调 + 返回全文
 */
"use strict";
const path = require("path");
const fs = require("fs");
const { detectCapability, decideExecution } = require("./models/model-manager.js");

// 默认本地模型文件名（用户 LoRA 合并 + 量化后的产物；见 scripts/lora-to-gguf.py）。
const DEFAULT_LLM_FILE = "qwen2.5-7b-hashmm-q4_k_m.gguf";
// 7B Q4_K_M 显存/内存门槛（经验值）：GPU 路径约需 6G 显存；纯 CPU 回退约需 8G 内存。
const LLM_MIN_VRAM_MB = 6144;
const LLM_MIN_MEM_MB = 8192;

// ───────────────────────── 纯函数（沙箱可单测） ─────────────────────────

/** 解析本地模型 gguf 的绝对路径。 */
function resolveModelPath(modelsDir, fileName) {
  return path.join(String(modelsDir || ""), String(fileName || DEFAULT_LLM_FILE));
}

/**
 * 拼装最终提示：把本地知识库片段（context）并入用户问题，形成"带证据的用户消息"。
 * 系统人设单独经 systemPrompt 传给会话，不混进来。无 context 时原样返回问题。
 */
function buildPrompt({ context = "", user = "" } = {}) {
  const u = String(user || "").trim();
  const c = String(context || "").trim();
  if (!c) return u;
  return (
    "请参考以下本地知识库片段回答（无关片段忽略；引用时标注[编号]，" +
    "事实需有出处，拿不准就说明依据不足，不要编造）：\n\n" +
    c +
    "\n\n问题：" +
    u
  );
}

/** 校验并裁剪生成参数到安全范围（防 UI 传入越界值把本机拖垮）。 */
function normalizeGenOptions(opts = {}) {
  const o = opts || {};
  const clamp = (v, lo, hi, dflt) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  };
  return {
    maxTokens: Math.round(clamp(o.maxTokens, 16, 4096, 1024)),
    temperature: clamp(o.temperature, 0, 2, 0.3),
    topP: clamp(o.topP, 0, 1, 0.9),
  };
}

/**
 * 本地推理可行性决策（复用 model-manager 的 device_match 逻辑 + 文件就绪检查）。
 * @returns {{ viable:boolean, target:"local"|"cloud", reason:string, ready:boolean, modelPath:string, tier:string }}
 */
function localViability({ hw = {}, modelsDir = "", fileName = DEFAULT_LLM_FILE, existsFn } = {}) {
  const cap = detectCapability(hw);
  const model = {
    name: "local-llm", file: fileName, type: "llm",
    minVramMB: LLM_MIN_VRAM_MB, minMemMB: LLM_MIN_MEM_MB, runtime: "llama.cpp",
  };
  const decision = decideExecution(model, cap);
  const mp = resolveModelPath(modelsDir, fileName);
  const _exists = existsFn || ((p) => { try { return fs.existsSync(p); } catch (_) { return false; } });
  const ready = _exists(mp);
  let reason = decision.reason;
  let viable = decision.target === "local" && ready;
  if (decision.target === "local" && !ready) reason = `硬件满足，但模型文件未就绪：${mp}`;
  return { viable, target: viable ? "local" : "cloud", reason, ready, modelPath: mp, tier: cap.tier };
}

// ───────────────────── 有状态：模型按需加载并缓存（真机） ─────────────────────

// 加载 7B 很慢，故缓存 {llama, model}；每次生成新建轻量 context+session（避免历史串味），用后释放。
let _runtime = null;   // { mod, llama, model, modelPath, gpu }

/** 运行时是否可用（node-llama-cpp 是否已安装）——装了才走本地，没装回退云端。 */
async function isRuntimeAvailable() {
  try { await import("node-llama-cpp"); return true; }
  catch (_) { return false; }
}

/** 确保模型已加载（幂等；modelPath 变化则重载）。opts.gpu 可强制 CPU。 */
async function ensureLoaded(modelPath, opts = {}) {
  if (!modelPath || !fs.existsSync(modelPath)) {
    throw new Error(`本地模型文件不存在：${modelPath}`);
  }
  if (_runtime && _runtime.modelPath === modelPath) return _runtime;
  // 切换模型：先释放旧的
  await unload();
  const mod = await import("node-llama-cpp");
  const getLlama = mod.getLlama;
  const llama = await getLlama(opts.gpu === false ? { gpu: false } : {});
  const model = await llama.loadModel({ modelPath });
  _runtime = { mod, llama, model, modelPath, gpu: opts.gpu !== false };
  return _runtime;
}

/**
 * 本地生成。onToken(textChunk) 流式回调（用于把 token 经 llm:delta 推给 renderer）。
 * @returns {Promise<string>} 完整答案文本
 */
async function generate({ modelPath, system = "", context = "", user = "", onToken, options = {} } = {}) {
  const rt = await ensureLoaded(modelPath, options);
  const { LlamaChatSession } = rt.mod;
  const ctx = await rt.model.createContext();
  try {
    const session = new LlamaChatSession({
      contextSequence: ctx.getSequence(),
      systemPrompt: String(system || "").trim() || undefined,
      // chatWrapper 默认 "auto"：从 GGUF 元数据识别 Qwen 模板，无需手填
    });
    const prompt = buildPrompt({ context, user });
    const gen = normalizeGenOptions(options);
    const text = await session.prompt(prompt, {
      maxTokens: gen.maxTokens,
      temperature: gen.temperature,
      topP: gen.topP,
      onTextChunk: (chunk) => { if (typeof onToken === "function" && chunk) onToken(chunk); },
    });
    return text;
  } finally {
    // 释放本次 context 的 KV 缓存（模型仍缓存，下次生成免重载）
    try { await ctx.dispose(); } catch (_) { /* 旧版本可能无 dispose，忽略 */ }
  }
}

/** 释放已加载的本地模型，回收显存/内存。 */
async function unload() {
  if (!_runtime) return { ok: true, unloaded: false };
  try { if (_runtime.model && _runtime.model.dispose) await _runtime.model.dispose(); } catch (_) { /* */ }
  try { if (_runtime.llama && _runtime.llama.dispose) await _runtime.llama.dispose(); } catch (_) { /* */ }
  _runtime = null;
  return { ok: true, unloaded: true };
}

/** 测试辅助：当前是否已加载。 */
function _isLoaded() { return !!_runtime; }

module.exports = {
  DEFAULT_LLM_FILE, LLM_MIN_VRAM_MB, LLM_MIN_MEM_MB,
  // 纯函数
  resolveModelPath, buildPrompt, normalizeGenOptions, localViability,
  // 运行时
  isRuntimeAvailable, ensureLoaded, generate, unload, _isLoaded,
};
