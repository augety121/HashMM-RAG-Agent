/**
 * desktop/semantic-status.js — 语义检索设置状态纯逻辑（V103.90）。
 *
 * 语义检索（ONNX 小模型 INT8，BM25 之上的语义层）现在状态只有一行字，用户搞不清：
 * 能不能用、模型下没下、有没有启用、**向量索引建到什么程度、跟知识库同步了没**。
 * 本模块把这些翻成清晰的步骤状态 + 向量覆盖率（已向量化切片 / 知识库总切片）。
 *
 * 纯函数、不抛异常、便于沙箱单测。
 */
"use strict";

/** 向量索引覆盖率：已向量化 vs 知识库总切片。 */
function coverageInfo(vectors, kbChunks) {
  const v = Math.max(0, Number(vectors) || 0);
  const k = Math.max(0, Number(kbChunks) || 0);
  if (k === 0) return { pct: 0, label: v > 0 ? `${v} 个向量` : "无向量", synced: v === 0, stale: false };
  const pct = Math.min(100, Math.round((v / k) * 100));
  const synced = v >= k;
  return {
    pct, vectors: v, kbChunks: k, synced,
    stale: v > 0 && v < k,
    label: synced ? `已同步（${v} 向量）` : v === 0 ? `未建索引（知识库 ${k} 切片）` : `部分同步 ${v}/${k}（${pct}%）`,
  };
}

/**
 * 由状态生成有序步骤。纯函数。
 * @param s {
 *   deviceOk, memGB, cores,
 *   ortInstalled,        // onnxruntime 是否编译进本包
 *   modelDownloaded,     // 语义模型是否已下载
 *   enabled,             // 是否已启用语义检索
 *   vectors,             // 已建向量数
 *   kbChunks,            // 知识库切片总数
 * }
 * @returns [{ id, title, state:"ok"|"todo"|"blocked"|"warn", detail, action? }]
 */
function buildSemanticSteps(s) {
  s = s || {};
  const steps = [];

  // 1) 设备能力
  steps.push(s.deviceOk
    ? { id: "device", title: "设备能力", state: "ok", detail: `可运行 INT8 小模型（内存 ${s.memGB || "?"}G / ${s.cores || "?"} 核）` }
    : { id: "device", title: "设备能力", state: "warn", detail: `配置较低（内存 ${s.memGB || "?"}G / ${s.cores || "?"} 核），建议保持 BM25 词法检索（零开销）` });

  // 2) 运行时（ONNX）
  steps.push(s.ortInstalled
    ? { id: "runtime", title: "ONNX 运行时", state: "ok", detail: "onnxruntime 已就绪" }
    : { id: "runtime", title: "ONNX 运行时", state: "blocked", detail: "onnxruntime 未编译进本安装包（可选原生组件）——保持 BM25 词法检索" });

  // 3) 模型
  if (!s.ortInstalled) {
    steps.push({ id: "model", title: "语义模型", state: "todo", detail: "需先有 ONNX 运行时" });
  } else {
    steps.push(s.modelDownloaded
      ? { id: "model", title: "语义模型", state: "ok", detail: "语义模型已下载（INT8 量化 ~24MB）" }
      : { id: "model", title: "语义模型", state: "todo", detail: "尚未下载语义模型", action: "点「下载模型」（约 24MB，一次即可）" });
  }

  // 4) 启用
  steps.push(s.enabled
    ? { id: "enabled", title: "启用状态", state: "ok", detail: "语义 + BM25 混合检索已开启" }
    : { id: "enabled", title: "启用状态", state: (s.ortInstalled && s.modelDownloaded) ? "todo" : "todo",
        detail: (s.ortInstalled && s.modelDownloaded) ? "模型就绪，点「启用」开启混合检索" : "完成上面的步骤后可启用" });

  // 5) 向量索引覆盖
  const cov = coverageInfo(s.vectors, s.kbChunks);
  let covState, covAction;
  if (!s.enabled) { covState = "todo"; }
  else if (cov.synced && (cov.kbChunks > 0 || cov.vectors > 0)) { covState = "ok"; }
  else if (cov.stale) { covState = "warn"; covAction = "知识库有新增/改动，点「重建向量索引」补齐"; }
  else { covState = "todo"; covAction = "点「重建向量索引」为知识库建立语义向量"; }
  steps.push({ id: "index", title: "向量索引", state: covState, detail: "向量覆盖：" + cov.label, action: covAction });

  return steps;
}

/** 总体状态。 */
function overallSemantic(s) {
  const steps = buildSemanticSteps(s);
  const blocked = steps.some(x => x.state === "blocked");
  const active = !!(s && s.enabled);
  const cov = coverageInfo(s && s.vectors, s && s.kbChunks);
  return {
    active, blocked,
    synced: cov.synced,
    summary: blocked ? "本包未含语义组件，使用 BM25"
           : active ? (cov.synced ? "语义检索已就绪" : "语义已启用，向量索引待补齐")
           : "语义检索未启用（当前 BM25 词法检索）",
  };
}

module.exports = { coverageInfo, buildSemanticSteps, overallSemantic };
