/**
 * semantic.js — 本地 RAG 二期：ONNX 语义检索核心（V77）。
 *
 * Marvis 同款分层：设备够 → 本地小模型语义检索；不够/没装 → BM25 降级。
 * 模型：bge-small-zh-v1.5 INT8 量化 ONNX（~24MB，首次启用时下载）。
 *
 * 设计要点（沙箱可测）：
 * - WordPiece tokenizer 纯 JS 实现（vocab 注入）；
 * - ONNX session 注入式（embed() 接收 runFn）——单测用 stub 验证管线；
 * - 向量索引 Float32Array + base64 序列化；
 * - hybridFuse：BM25 与语义双路 RRF 融合（V74 同思想）。
 */
"use strict";

/* ───────── WordPiece tokenizer（BERT 中文系，纯 JS） ───────── */
class WordPiece {
  /** @param vocab Map<string, number> 或 {token: id} 对象 */
  constructor(vocab) {
    this.vocab = vocab instanceof Map ? vocab : new Map(Object.entries(vocab || {}));
    this.cls = this.vocab.get("[CLS]") ?? 101;
    this.sep = this.vocab.get("[SEP]") ?? 102;
    this.unk = this.vocab.get("[UNK]") ?? 100;
    this.maxLen = 512;
  }

  static fromVocabText(text) {
    const m = new Map();
    String(text || "").split("\n").forEach((line, i) => {
      const t = line.replace(/\r$/, "");
      if (t) m.set(t, i);
    });
    return new WordPiece(m);
  }

  /** 基础切分：中文逐字、英文/数字成词、其余按符号 */
  _basic(text) {
    const out = [];
    const s = String(text || "").toLowerCase();
    let buf = "";
    const flush = () => { if (buf) { out.push(buf); buf = ""; } };
    for (const ch of s) {
      if (/[\u4e00-\u9fff]/.test(ch)) { flush(); out.push(ch); }
      else if (/[a-z0-9]/.test(ch)) buf += ch;
      else { flush(); if (/\S/.test(ch)) out.push(ch); }
    }
    flush();
    return out;
  }

  /** WordPiece 贪心最长匹配（##前缀子词） */
  _wordpiece(word) {
    if (this.vocab.has(word)) return [this.vocab.get(word)];
    const ids = [];
    let start = 0;
    while (start < word.length) {
      let end = word.length, cur = null;
      while (start < end) {
        const piece = (start > 0 ? "##" : "") + word.slice(start, end);
        if (this.vocab.has(piece)) { cur = this.vocab.get(piece); break; }
        end--;
      }
      if (cur === null) return [this.unk];
      ids.push(cur);
      start = end;
    }
    return ids;
  }

  /** 编码为 BERT 输入：{inputIds, attentionMask, tokenTypeIds} */
  encode(text, maxLen) {
    const cap = Math.min(maxLen || this.maxLen, this.maxLen);
    const ids = [this.cls];
    for (const w of this._basic(text)) {
      for (const id of this._wordpiece(w)) {
        if (ids.length >= cap - 1) break;
        ids.push(id);
      }
      if (ids.length >= cap - 1) break;
    }
    ids.push(this.sep);
    return {
      inputIds: ids,
      attentionMask: new Array(ids.length).fill(1),
      tokenTypeIds: new Array(ids.length).fill(0),
    };
  }
}

/* ───────── 向量工具 ───────── */
function l2normalize(vec) {
  let norm = 0;
  for (let i = 0; i < vec.length; i++) norm += vec[i] * vec[i];
  norm = Math.sqrt(norm) || 1;
  const out = new Float32Array(vec.length);
  for (let i = 0; i < vec.length; i++) out[i] = vec[i] / norm;
  return out;
}

function cosine(a, b) {
  let dot = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) dot += a[i] * b[i];
  return dot;   // 已 L2 归一化时点积=余弦
}

/** mean pooling：last_hidden_state [1, seq, dim] + attention mask → [dim] */
function meanPool(hidden, mask, dim) {
  const out = new Float32Array(dim);
  let cnt = 0;
  for (let t = 0; t < mask.length; t++) {
    if (!mask[t]) continue;
    cnt++;
    for (let d = 0; d < dim; d++) out[d] += hidden[t * dim + d];
  }
  if (cnt > 0) for (let d = 0; d < dim; d++) out[d] /= cnt;
  return out;
}

/**
 * 嵌入一段文本。runFn 注入（生产=onnx session.run 包装，测试=stub）：
 *   runFn({inputIds, attentionMask, tokenTypeIds}) -> { data: Float32Array|number[], dims: [1, seq, dim] }
 */
async function embed(text, tokenizer, runFn, maxLen = 256) {
  const enc = tokenizer.encode(text, maxLen);
  const out = await runFn(enc);
  const dim = out.dims[2];
  return l2normalize(meanPool(out.data, enc.attentionMask, dim));
}

/* ───────── 语义索引（与 LocalIndex 的 chunks 平行存向量） ───────── */
class SemanticIndex {
  constructor(dim = 512) { this.dim = dim; this.vecs = []; /* Float32Array[] 与 chunk id 对齐 */ }

  setVec(chunkId, vec) { this.vecs[chunkId] = vec; }

  search(queryVec, topK = 5) {
    const scored = [];
    for (let id = 0; id < this.vecs.length; id++) {
      const v = this.vecs[id];
      if (!v) continue;
      scored.push([id, cosine(queryVec, v)]);
    }
    scored.sort((a, b) => b[1] - a[1]);
    return scored.slice(0, topK).map(([id, score]) => ({ id, score: Math.round(score * 1000) / 1000 }));
  }

  toJSON() {
    return {
      v: 1, dim: this.dim,
      vecs: this.vecs.map(v => v ? Buffer.from(v.buffer, v.byteOffset, v.byteLength).toString("base64") : null),
    };
  }

  static fromJSON(obj) {
    const idx = new SemanticIndex(obj && obj.dim || 512);
    if (obj && obj.v === 1) {
      idx.vecs = (obj.vecs || []).map(b64 => {
        if (!b64) return null;
        const buf = Buffer.from(b64, "base64");
        return new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
      });
    }
    return idx;
  }

  stat() { return { vectors: this.vecs.filter(Boolean).length, dim: this.dim }; }
}

/* ───────── 双路融合（BM25 + 语义，RRF——V74 同思想） ───────── */
function hybridFuse(bm25Ids, semIds, topK = 5, k = 60) {
  const scores = new Map();
  for (const [rank, id] of bm25Ids.entries())
    scores.set(id, (scores.get(id) || 0) + 1 / (k + rank + 1));
  for (const [rank, id] of semIds.entries())
    scores.set(id, (scores.get(id) || 0) + 1 / (k + rank + 1));
  return [...scores.entries()].sort((a, b) => b[1] - a[1]).slice(0, topK)
    .map(([id, s]) => ({ id, fused: Math.round(s * 10000) / 10000 }));
}

/* ───────── 轻量重排（V80 方案B：词覆盖率+词距紧凑度+位置，零模型） ───────── */
/**
 * lightRerank(queryTokens, candidates) — 融合后的最后一道排序。
 * 特征（加权和）：
 *  - coverage 0.5：查询 token 被命中的比例（覆盖越全越相关）
 *  - proximity 0.3：命中词在文本中的紧凑度（首末命中位置跨度越小越好）
 *  - position 0.2：首个命中越靠前越好
 * candidates: [{text, ...}]；返回按 rerank 分降序的新数组（带 _rerank 字段）。
 */
function lightRerank(queryTokens, candidates) {
  const qs = [...new Set(queryTokens || [])];
  if (!qs.length) return (candidates || []).slice();
  const scored = (candidates || []).map(c => {
    const text = String(c.text || "").toLowerCase();
    let hit = 0, first = -1, last = -1;
    for (const t of qs) {
      const pos = text.indexOf(t);
      if (pos >= 0) {
        hit++;
        if (first < 0 || pos < first) first = pos;
        if (pos > last) last = pos;
      }
    }
    const coverage = hit / qs.length;
    const span = (hit >= 2 && last > first) ? (last - first) : 0;
    const proximity = hit >= 2 ? Math.max(0, 1 - span / Math.max(text.length, 1)) : (hit === 1 ? 0.5 : 0);
    const position = first >= 0 ? Math.max(0, 1 - first / Math.max(text.length, 1)) : 0;
    const score = 0.5 * coverage + 0.3 * proximity + 0.2 * position;
    return { ...c, _rerank: Math.round(score * 1000) / 1000 };
  });
  scored.sort((a, b) => b._rerank - a._rerank);
  return scored;
}

/* ───────── 设备检测（Marvis llm_device_match 同思路，本地版） ───────── */
function deviceCheck(os) {
  const memGB = os.totalmem() / (1024 ** 3);
  const cores = os.cpus().length;
  const ok = memGB >= 4 && cores >= 2;     // INT8 小模型门槛很低
  return {
    ok, memGB: Math.round(memGB * 10) / 10, cores,
    advice: ok ? "本机可运行本地语义模型（INT8 量化 ~24MB）"
               : "本机配置较低，建议保持 BM25 词法检索（零开销）",
  };
}

module.exports = { WordPiece, l2normalize, cosine, meanPool, embed, SemanticIndex, hybridFuse, lightRerank, deviceCheck };
