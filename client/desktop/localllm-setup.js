/**
 * desktop/localllm-setup.js — 本地模型安装向导纯逻辑（V103.90）。
 *
 * 本地模型（自训 7B → GGUF）现在只有一个复选框，用户根本不知道怎么把它跑起来：
 * 要装推理运行时、要把 GGUF 放对目录、硬件还得够。大厂做法是一个**安装向导**，
 * 一步步显示：硬件检查 → 运行时 → 模型文件 → 校验 → 就绪，每步给状态和可操作指引。
 *
 * 本模块是向导的纯逻辑核心（把 llm:localStatus 的状态翻成有序步骤 + 状态 + 指引，
 * 以及 GGUF 文件大小校验），不碰 IPC/FS，便于沙箱单测。
 */
"use strict";

// 一个 7B Q4_K_M GGUF 通常 4~5GB；低于这个量级基本是下错/没下完。
const MIN_GGUF_BYTES = 1.5 * 1024 * 1024 * 1024;   // 1.5GB 保守下限

function _gb(mb) { return mb ? (mb / 1024).toFixed(1) + " GB" : "未知"; }

/**
 * 校验 GGUF 模型文件是否像样（按大小粗判，不解析格式）。
 * @returns { ok, reason }
 */
function verifyModelFile(info) {
  info = info || {};
  if (!info.exists) return { ok: false, reason: "未找到模型文件" };
  const size = Number(info.size) || 0;
  const min = Number(info.minBytes) || MIN_GGUF_BYTES;
  if (size < min) return { ok: false, reason: `文件偏小（${(size / 1024 / 1024 / 1024).toFixed(2)}GB），可能没下完或下错文件` };
  if (info.name && !/\.gguf$/i.test(info.name)) return { ok: false, reason: "扩展名不是 .gguf" };
  return { ok: true, reason: "" };
}

/**
 * 由状态生成有序安装步骤。纯函数。
 * @param status {
 *   tier: "low"|"mid"|"high",
 *   hw: { totalVramMB, totalMemMB, hasGpu },
 *   minVramMB, minMemMB,
 *   runtimeAvailable: bool,
 *   ready: bool,             // 模型文件存在
 *   viable: bool,
 *   modelPath, modelsDir, modelFile,
 *   modelSize,               // 模型文件字节数（若已知）
 * }
 * @returns [{ id, title, state:"ok"|"todo"|"blocked"|"warn", detail, action? }]
 */
function buildSetupSteps(status) {
  const s = status || {};
  const hw = s.hw || {};
  const steps = [];

  // 1) 硬件
  const vram = Number(hw.totalVramMB) || 0;
  const mem = Number(hw.totalMemMB) || 0;
  const minV = Number(s.minVramMB) || 0;
  const minM = Number(s.minMemMB) || 0;
  let hwState, hwDetail;
  if (hw.hasGpu && (!minV || vram >= minV)) {
    hwState = "ok"; hwDetail = `检测到 GPU 显存 ${_gb(vram)}，满足要求`;
  } else if (!minM || mem >= minM) {
    hwState = "warn"; hwDetail = `无足够 GPU 显存，将走 CPU 推理（较慢）。内存 ${_gb(mem)}`;
  } else {
    hwState = "blocked"; hwDetail = `硬件偏弱（显存 ${_gb(vram)} / 内存 ${_gb(mem)}），7B 模型可能跑不动或很慢`;
  }
  steps.push({ id: "hardware", title: "硬件检查", state: hwState, detail: hwDetail });

  // 2) 运行时
  steps.push(s.runtimeAvailable
    ? { id: "runtime", title: "推理运行时", state: "ok", detail: "node-llama-cpp 运行时已就绪" }
    : { id: "runtime", title: "推理运行时", state: "todo",
        detail: "未检测到本地推理运行时（node-llama-cpp 是可选原生组件，部分打包未含）",
        action: "在客户端安装目录执行 npm i node-llama-cpp，或使用带本地推理的完整安装包" });

  // 3) 模型文件
  if (s.ready) {
    const v = s.modelSize ? verifyModelFile({ exists: true, size: s.modelSize, name: s.modelFile }) : { ok: true, reason: "" };
    steps.push(v.ok
      ? { id: "model", title: "模型文件", state: "ok", detail: `已就绪：${s.modelFile || s.modelPath || ""}` }
      : { id: "model", title: "模型文件", state: "warn", detail: v.reason, action: `重新放置完整的 GGUF 到 ${s.modelsDir || ""}` });
  } else {
    steps.push({ id: "model", title: "模型文件", state: "todo",
      detail: `未发现模型文件 ${s.modelFile || ""}`,
      action: `把转换好的 GGUF 放到模型目录：${s.modelsDir || "(模型目录)"}（用 scripts/lora-to-gguf.py 把你的 LoRA 合并量化成 q4_k_m）` });
  }

  // 4) 就绪
  const ready = s.runtimeAvailable && s.ready && s.viable;
  steps.push({ id: "ready", title: "就绪", state: ready ? "ok" : "todo",
    detail: ready ? "本地模型已就绪，可勾选「本地模型」全程离线作答（数据不出端）"
                  : "完成上面的步骤后即可开启本地模型" });

  return steps;
}

/** 总体状态：已就绪 / 还差几步。 */
function overallState(status) {
  const steps = buildSetupSteps(status);
  const blocked = steps.some(x => x.state === "blocked");
  const todo = steps.filter(x => x.state === "todo").length;
  const ready = steps[steps.length - 1].state === "ok";
  return {
    ready, blocked, pending: todo,
    summary: ready ? "本地模型已就绪" : blocked ? "硬件可能不达标" : `还差 ${todo} 步`,
  };
}

module.exports = { MIN_GGUF_BYTES, verifyModelFile, buildSetupSteps, overallState };
