/**
 * localrag.js — HashMM 桌面端本地 RAG 核心（V103.90 深化版）。
 *
 * Marvis 同款分层思路：本地轻量检索 + 云端 LLM = 不依赖远程后端的完整 RAG。
 * BM25 词法检索（纯 JS 零模型，任何 CPU 秒级），中文 2-gram + 英文小写词。
 *
 * V103.90 把它从"一摄了之"升级为可管理的知识库引擎，新增：
 *   - 文件注册表 files（path -> {folder, mtime, size, chunks}）：知道每个文件何时摄取、多大、几片。
 *   - 去重摄取 addDocument：再次摄取同一文件先删旧切片（修复"重复摄取→切片翻倍"的真实 bug）。
 *   - 单文件/单文件夹移除 removeFile / removeFolder（不再只能整库清空）。
 *   - 增量同步 diffFolder（纯函数）：对比磁盘现状与已索引，算出 新增/改动/删除/未变，只重摄变化的。
 *   - fileList / folderList：供 UI 按文件夹分组展示。
 * 向后兼容：旧 localrag.json（无 files 注册表）自动从 chunks 派生，老用户无缝升级。
 *
 * 纯逻辑模块（无 Electron 依赖）→ node 可直接单测。
 */
"use strict";

/** 中文 2-gram + 英文/数字词 分词（轻量、无词典） */
function tokenize(text) {
  const tokens = [];
  const s = String(text || "").toLowerCase();
  for (const m of s.matchAll(/[a-z0-9_]+/g)) {
    if (m[0].length >= 2) tokens.push(m[0]);
  }
  const han = s.match(/[\u4e00-\u9fff]+/g) || [];
  for (const seg of han) {
    if (seg.length === 1) { tokens.push(seg); continue; }
    for (let i = 0; i < seg.length - 1; i++) tokens.push(seg.slice(i, i + 2));
  }
  return tokens;
}

/** 文本分块：约 chunkSize 字、overlap 重叠，按换行优先断开 */
function chunkText(text, chunkSize = 700, overlap = 120) {
  const s = String(text || "");
  if (s.length <= chunkSize) return s.trim() ? [s.trim()] : [];
  const chunks = [];
  let start = 0;
  while (start < s.length) {
    let end = Math.min(start + chunkSize, s.length);
    if (end < s.length) {
      const nl = s.lastIndexOf("\n", end);
      if (nl > start + chunkSize * 0.5) end = nl;
    }
    const piece = s.slice(start, end).trim();
    if (piece) chunks.push(piece);
    if (end >= s.length) break;
    start = Math.max(end - overlap, start + 1);
  }
  return chunks;
}

/** 规范化路径分隔符，便于跨平台前缀比较 */
function _norm(p) { return String(p || "").replace(/\\/g, "/"); }

/** 判断 file 是否在 folder 之下（按规范化路径前缀） */
function _under(file, folder) {
  const f = _norm(folder).replace(/\/+$/, "");
  const x = _norm(file);
  return x === f || x.startsWith(f + "/");
}

/** BM25 索引 + 文件管理 */
class LocalIndex {
  constructor() {
    this.chunks = [];          // [{id, file, text, len}]
    this.df = new Map();       // token -> 含该词的 chunk 数
    this.tf = [];              // chunkId -> Map(token -> 次数)
    this.totalLen = 0;
    this.files = new Map();    // file -> {folder, mtime, size, chunks}
    this.k1 = 1.5;
    this.b = 0.75;
  }

  // 追加一个切片（内部）；返回是否真的加了
  _pushChunk(file, piece) {
    const toks = tokenize(piece);
    if (!toks.length) return false;
    const id = this.chunks.length;
    const tfm = new Map();
    for (const t of toks) tfm.set(t, (tfm.get(t) || 0) + 1);
    for (const t of tfm.keys()) this.df.set(t, (this.df.get(t) || 0) + 1);
    this.chunks.push({ id, file, text: piece, len: toks.length });
    this.tf.push(tfm);
    this.totalLen += toks.length;
    return true;
  }

  // 从现有 chunks 全量重建 df/tf/totalLen/id（移除后保证 BM25 统计正确）
  _reindex() {
    const old = this.chunks;
    this.chunks = []; this.df = new Map(); this.tf = []; this.totalLen = 0;
    for (const c of old) this._pushChunk(c.file, c.text);
  }

  /**
   * 摄取一个文档（**替换语义**：同一 file 再次摄取会先删旧切片，修复重复摄取翻倍）。
   * meta: { folder, mtime, size }
   */
  addDocument(file, text, meta) {
    meta = meta || {};
    if (this.files.has(file)) {
      this.chunks = this.chunks.filter(c => c.file !== file);   // 先删旧切片
    }
    let added = 0;
    for (const piece of chunkText(text)) {
      this.chunks.push({ id: -1, file, text: piece, len: 0 });  // 占位，随后统一 reindex
      added++;
    }
    this._reindex();
    const prev = this.files.get(file) || {};
    this.files.set(file, {
      folder: meta.folder != null ? meta.folder : (prev.folder || ""),
      mtime: meta.mtime != null ? meta.mtime : (prev.mtime || 0),
      size: meta.size != null ? meta.size : (prev.size || 0),
      chunks: added,
    });
    return added;
  }

  /** 移除单个文件的所有切片。返回移除的切片数。 */
  removeFile(file) {
    if (!this.files.has(file) && !this.chunks.some(c => c.file === file)) return 0;
    const before = this.chunks.length;
    this.chunks = this.chunks.filter(c => c.file !== file);
    const removed = before - this.chunks.length;
    this.files.delete(file);
    this._reindex();
    return removed;
  }

  /** 移除某文件夹下所有文件。返回 {files, chunks}。 */
  removeFolder(folder) {
    const victims = this.fileList().filter(f => _under(f.file, folder)).map(f => f.file);
    if (!victims.length) return { files: 0, chunks: 0 };
    const before = this.chunks.length;
    const victimSet = new Set(victims);
    this.chunks = this.chunks.filter(c => !victimSet.has(c.file));
    const removedChunks = before - this.chunks.length;
    for (const v of victims) this.files.delete(v);
    this._reindex();
    return { files: victims.length, chunks: removedChunks };
  }

  get avgLen() { return this.chunks.length ? this.totalLen / this.chunks.length : 0; }

  search(query, topK = 5) {
    const qToks = [...new Set(tokenize(query))];
    if (!qToks.length || !this.chunks.length) return [];
    const N = this.chunks.length;
    const scores = new Map();
    for (const t of qToks) {
      const df = this.df.get(t);
      if (!df) continue;
      const idf = Math.log(1 + (N - df + 0.5) / (df + 0.5));
      for (let cid = 0; cid < N; cid++) {
        const f = this.tf[cid].get(t);
        if (!f) continue;
        const c = this.chunks[cid];
        const denom = f + this.k1 * (1 - this.b + this.b * (c.len / this.avgLen));
        scores.set(cid, (scores.get(cid) || 0) + idf * ((f * (this.k1 + 1)) / denom));
      }
    }
    return [...scores.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, topK)
      .map(([cid, score]) => ({
        score: Math.round(score * 1000) / 1000,
        file: this.chunks[cid].file,
        text: this.chunks[cid].text,
      }));
  }

  /** 已索引文件清单（注册表优先；旧库无注册表则从 chunks 派生）。 */
  fileList() {
    if (this.files.size) {
      return [...this.files.entries()].map(([file, m]) => ({
        file, folder: m.folder || "", chunks: m.chunks || 0,
        size: m.size || 0, mtime: m.mtime || 0,
      }));
    }
    const byFile = new Map();
    for (const c of this.chunks) byFile.set(c.file, (byFile.get(c.file) || 0) + 1);
    return [...byFile.entries()].map(([file, chunks]) => ({
      file, folder: "", chunks, size: 0, mtime: 0,
    }));
  }

  /** 按文件夹聚合（folder 取注册的 folder；旧库归到"未分组"）。 */
  folderList() {
    const byFolder = new Map();
    for (const f of this.fileList()) {
      const key = f.folder || "(未分组)";
      const g = byFolder.get(key) || { folder: key, files: 0, chunks: 0, size: 0 };
      g.files += 1; g.chunks += f.chunks; g.size += f.size;
      byFolder.set(key, g);
    }
    return [...byFolder.values()].sort((a, b) => b.chunks - a.chunks);
  }

  /**
   * 增量同步对比（纯函数）：磁盘当前文件 vs 已索引，算出要做什么。
   * @param folder 文件夹路径（只比对该文件夹下的索引文件）
   * @param currentFiles [{path, mtime, size}] 磁盘上现存的文件
   * @returns { added:[path], changed:[path], removed:[path], unchanged:[path] }
   */
  diffFolder(folder, currentFiles) {
    const indexed = new Map();
    for (const f of this.fileList()) {
      if (_under(f.file, folder)) indexed.set(_norm(f.file), { mtime: f.mtime || 0, size: f.size || 0 });
    }
    const cur = new Map();
    for (const f of (currentFiles || [])) {
      cur.set(_norm(f.path), { mtime: Number(f.mtime) || 0, size: Number(f.size) || 0 });
    }
    const added = [], changed = [], unchanged = [], removed = [];
    for (const [p, m] of cur.entries()) {
      if (!indexed.has(p)) { added.push(p); continue; }
      const im = indexed.get(p);
      const mtimeChanged = m.mtime && im.mtime && m.mtime !== im.mtime;
      const sizeChanged = m.size !== im.size;
      if (mtimeChanged || sizeChanged) changed.push(p);
      else unchanged.push(p);
    }
    for (const p of indexed.keys()) if (!cur.has(p)) removed.push(p);
    return { added, changed, removed, unchanged };
  }

  /** 序列化（存 userData） */
  toJSON() {
    return {
      v: 2,
      chunks: this.chunks,
      df: [...this.df.entries()],
      tf: this.tf.map(m => [...m.entries()]),
      totalLen: this.totalLen,
      files: [...this.files.entries()],
    };
  }

  static fromJSON(obj) {
    const idx = new LocalIndex();
    if (!obj) return idx;
    if (obj.v !== 1 && obj.v !== 2) return idx;
    idx.chunks = obj.chunks || [];
    idx.df = new Map(obj.df || []);
    idx.tf = (obj.tf || []).map(e => new Map(e));
    idx.totalLen = obj.totalLen || 0;
    if (obj.files && obj.files.length) {
      idx.files = new Map(obj.files);
    } else {
      const byFile = new Map();
      for (const c of idx.chunks) byFile.set(c.file, (byFile.get(c.file) || 0) + 1);
      for (const [file, chunks] of byFile.entries()) {
        idx.files.set(file, { folder: "", mtime: 0, size: 0, chunks });
      }
    }
    return idx;
  }

  stat() {
    let size = 0;
    for (const m of this.files.values()) size += m.size || 0;
    return {
      files: this.files.size || new Set(this.chunks.map(c => c.file)).size,
      chunks: this.chunks.length,
      terms: this.df.size,
      folders: this.folderList().length,
      bytes: size,
    };
  }
}

module.exports = { tokenize, chunkText, LocalIndex, _under, _norm };
