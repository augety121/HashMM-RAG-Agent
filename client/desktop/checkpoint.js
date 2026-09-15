// checkpoint.js — V300 第二期：执行检查点 / Rewind（对标 Claude Code 招牌能力）
//
// 为什么要它：Agent 敢去改用户的文件/跑命令，前提是"出错能回滚"。此前 write_file / 危险 shell
// 命令一旦执行就不可逆，一次误操作就失去用户信任。本模块在"有副作用的步骤前"给受影响文件拍快照，
// 存进检查点仓；任务出错或用户不满意时，一键 rewind 还原到某个检查点。
//
// 范围（务实、可测）：
//   - write_file：写入前快照该文件的原内容（不存在则记录"原本不存在"，回滚=删除）。
//   - 危险 shell（rm/move/rename/del 等触及具体路径的）：执行前快照命令里能解析出的目标路径。
//   - 不追踪整盘/通配的破坏（rm -rf /* 这类本就被硬拦，不在可回滚范围）——诚实标注不可回滚。
//
// 存储：用户数据目录下 checkpoints/<taskId>/，每个检查点一个 JSON（元数据）+ 快照文件副本。
// 幂等：同一 (op, path, contentHash) 不重复快照。上限：单文件 <5MB、单任务 <50 检查点，超限跳过并标注。

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const os = require("os");

const MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024;   // 单文件快照上限 5MB（大文件不快照，标注不可回滚）
const MAX_CHECKPOINTS_PER_TASK = 50;

function _root() {
  // 与主程序同一 userData 根；测试可用 HASHMM_CKPT_DIR 覆盖
  const base = process.env.HASHMM_CKPT_DIR
    || (process.env.APPDATA || path.join(os.homedir(), ".hashmm"));
  return path.join(base, "hashmm-checkpoints");
}

function _taskDir(taskId) {
  const d = path.join(_root(), String(taskId || "default").replace(/[^\w.\-]/g, "_"));
  fs.mkdirSync(d, { recursive: true });
  return d;
}

function _sha16(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex").slice(0, 16);
}

// ── 从 shell 命令里尽力解析出会被破坏的目标路径 ─────────────────────────
// 保守：只认能明确解析的路径，解析不出就返回空（宁可不快照也不误报）。
function extractTargetPaths(cmd) {
  const s = String(cmd || "");
  const out = new Set();
  // 引号包裹的路径
  for (const m of s.matchAll(/["']([^"']+?)["']/g)) {
    const p = m[1].trim();
    if (p && (path.isAbsolute(p) || p.includes("/") || p.includes("\\"))) out.add(p);
  }
  // rm/del/move/copy/ren 后跟的裸路径（简单形态）
  const verb = s.match(/\b(rm|del|erase|move|mv|copy|cp|ren|rename)\b\s+(.+)/i);
  if (verb) {
    for (const tok of verb[2].split(/\s+/)) {
      const p = tok.replace(/^["']|["']$/g, "");
      if (p && !p.startsWith("-") && !p.startsWith("/") && (p.includes("/") || p.includes("\\") || path.isAbsolute(p))) {
        out.add(p);
      } else if (p && path.isAbsolute(p)) {
        out.add(p);
      }
    }
  }
  // 过滤通配/整盘（不可安全快照）
  return [...out].filter((p) => !/[*?]/.test(p) && p !== "/" && !/^[a-z]:\\?$/i.test(p));
}

// ── 拍一个检查点：before 某个有副作用的操作 ─────────────────────────────
// op: "write_file" | "shell" ；paths: 受影响的绝对路径数组；meta: 附加信息（命令文本等）
// 返回 { id, snapshots:[{path, existed, hash, size, snapFile|null, skipped?}] , reversible }
function createCheckpoint(taskId, op, paths, meta = {}) {
  const dir = _taskDir(taskId);
  // 上限保护
  const existing = listCheckpoints(taskId);
  if (existing.length >= MAX_CHECKPOINTS_PER_TASK) {
    return { id: null, reversible: false, reason: "检查点数量已达上限", snapshots: [] };
  }
  const id = Date.now().toString(36) + crypto.randomBytes(3).toString("hex");
  const snapDir = path.join(dir, id);
  fs.mkdirSync(snapDir, { recursive: true });
  const snapshots = [];
  let reversible = true;
  for (const p of (paths || [])) {
    let abs;
    try { abs = path.resolve(p); } catch (_e) { continue; }
    let existed = false, size = 0, hash = "", snapFile = null, skipped = null;
    try {
      const st = fs.statSync(abs);
      if (st.isDirectory()) { skipped = "目录不快照（内容可能过大）"; reversible = false; }
      else if (st.size > MAX_SNAPSHOT_BYTES) { existed = true; size = st.size; skipped = "文件超过 5MB，未快照"; reversible = false; }
      else {
        const buf = fs.readFileSync(abs);
        existed = true; size = st.size; hash = _sha16(buf);
        snapFile = path.join(snapDir, hash + path.extname(abs));
        fs.writeFileSync(snapFile, buf);
      }
    } catch (_e) {
      // 文件不存在 → 记录"原本不存在"，回滚 = 删除新建的
      existed = false;
    }
    snapshots.push({ path: abs, existed, hash, size, snapFile, skipped });
  }
  const record = {
    id, taskId: String(taskId), op, meta, snapshots, reversible,
    createdAt: Date.now(), restored: false,
  };
  fs.writeFileSync(path.join(dir, id + ".json"), JSON.stringify(record, null, 2), "utf-8");
  return record;
}

// ── 回滚到某个检查点：把快照写回、把"原本不存在"的新建物删除 ──────────────
function rewindTo(taskId, checkpointId) {
  const dir = _taskDir(taskId);
  const metaPath = path.join(dir, checkpointId + ".json");
  let rec;
  try { rec = JSON.parse(fs.readFileSync(metaPath, "utf-8")); }
  catch (_e) { return { ok: false, error: "检查点不存在" }; }
  const results = [];
  for (const snap of rec.snapshots || []) {
    try {
      if (snap.skipped) { results.push({ path: snap.path, ok: false, note: snap.skipped }); continue; }
      if (snap.existed && snap.snapFile && fs.existsSync(snap.snapFile)) {
        fs.mkdirSync(path.dirname(snap.path), { recursive: true });
        fs.copyFileSync(snap.snapFile, snap.path);           // 还原原内容
        results.push({ path: snap.path, ok: true, action: "restored" });
      } else if (!snap.existed) {
        if (fs.existsSync(snap.path)) fs.unlinkSync(snap.path);  // 原本不存在 → 删掉新建物
        results.push({ path: snap.path, ok: true, action: "removed" });
      } else {
        results.push({ path: snap.path, ok: false, note: "快照缺失，无法还原" });
      }
    } catch (e) { results.push({ path: snap.path, ok: false, note: (e && e.message) || String(e) }); }
  }
  rec.restored = true; rec.restoredAt = Date.now();
  try { fs.writeFileSync(metaPath, JSON.stringify(rec, null, 2), "utf-8"); } catch (_e) {}
  const okCount = results.filter((r) => r.ok).length;
  return { ok: okCount > 0, restored: okCount, total: results.length, results };
}

// ── 列出某任务的检查点（新→旧）────────────────────────────────────────
function listCheckpoints(taskId) {
  const dir = _taskDir(taskId);
  let files = [];
  try { files = fs.readdirSync(dir).filter((f) => f.endsWith(".json")); } catch (_e) { return []; }
  const out = [];
  for (const f of files) {
    try {
      const rec = JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8"));
      out.push({
        id: rec.id, op: rec.op, createdAt: rec.createdAt, restored: !!rec.restored,
        reversible: !!rec.reversible, fileCount: (rec.snapshots || []).length,
        summary: rec.meta && rec.meta.summary ? String(rec.meta.summary).slice(0, 120) : rec.op,
      });
    } catch (_e) { /* 跳过坏文件 */ }
  }
  return out.sort((a, b) => b.createdAt - a.createdAt);
}

// ── 清理某任务的检查点（任务成功且用户确认后可清）────────────────────────
function clearCheckpoints(taskId) {
  const dir = _taskDir(taskId);
  try { fs.rmSync(dir, { recursive: true, force: true }); return { ok: true }; }
  catch (e) { return { ok: false, error: (e && e.message) || String(e) }; }
}

module.exports = {
  extractTargetPaths, createCheckpoint, rewindTo, listCheckpoints, clearCheckpoints,
  MAX_SNAPSHOT_BYTES, MAX_CHECKPOINTS_PER_TASK,
};
