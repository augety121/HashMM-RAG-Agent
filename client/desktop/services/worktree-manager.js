/**
 * HashMM managed Git worktrees (V337).
 *
 * Safety properties:
 * - Git is invoked with execFile/spawn argument arrays; no shell interpolation.
 * - Destination paths are generated below a HashMM-owned root.
 * - Removal is refused for dirty, active, permanent, unregistered, or detached
 *   worktrees containing commits not anchored by a branch.
 * - Optional local-change transfer is bounded and never removes the source.
 * - .worktreeinclude copies only ignored regular files, skips symlinks, never
 *   overwrites checkout files, and enforces file/byte caps.
 */
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile, spawn } = require("child_process");

const REGISTRY_VERSION = 1;
const DEFAULT_KEEP = 15;
const MAX_PATCH_BYTES = 20 * 1024 * 1024;
const MAX_INCLUDE_BYTES = 64 * 1024;
const MAX_COPY_FILES = 200;
const MAX_COPY_FILE_BYTES = 10 * 1024 * 1024;
const MAX_COPY_TOTAL_BYTES = 50 * 1024 * 1024;

class WorktreeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "WorktreeError";
    this.code = code;
  }
}

function _fail(code, message) { throw new WorktreeError(code, message); }

function _within(root, candidate) {
  const rel = path.relative(_realIfExists(root), _realIfExists(candidate));
  return rel === "" || (!path.isAbsolute(rel) && rel !== ".." && !rel.startsWith(".." + path.sep));
}

function _realIfExists(value) {
  const resolved = path.resolve(String(value || ""));
  try {
    const real = typeof fs.realpathSync.native === "function" ? fs.realpathSync.native(resolved) : fs.realpathSync(resolved);
    return process.platform === "win32" ? real.replace(/^\\\\\?\\/, "") : real;
  } catch (_e) { return resolved; }
}

function _key(value) {
  const resolved = _realIfExists(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function defaultManagedRoot(opts = {}) {
  if (opts.managedRoot) return path.resolve(String(opts.managedRoot));
  if (opts.userData) return path.resolve(String(opts.userData), "worktrees");
  return path.join(path.resolve(opts.homeDir || os.homedir()), ".hashmm", "worktrees");
}

function _git(cwd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    execFile("git", ["-C", cwd, ...args], {
      windowsHide: true,
      timeout: opts.timeout || 30000,
      maxBuffer: opts.maxBuffer || 32 * 1024 * 1024,
      encoding: "utf8",
    }, (error, stdout, stderr) => {
      if (!error) return resolve(String(stdout || ""));
      const detail = String(stderr || error.message || "git failed").trim().slice(-2000);
      const wrapped = new WorktreeError(opts.code || "GIT_ERROR", detail || "Git 操作失败");
      wrapped.cause = error;
      reject(wrapped);
    });
  });
}

function _gitInput(cwd, args, input, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn("git", ["-C", cwd, ...args], {
      windowsHide: true, stdio: ["pipe", "pipe", "pipe"], shell: false,
    });
    let stdout = "", stderr = "", settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      try { child.kill(); } catch (_e) { /* */ }
      settled = true;
      reject(new WorktreeError(opts.code || "GIT_TIMEOUT", "Git 操作超时"));
    }, opts.timeout || 30000);
    child.stdout.on("data", (chunk) => { if (stdout.length < 2_000_000) stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { if (stderr.length < 2_000_000) stderr += chunk.toString("utf8"); });
    child.on("error", (error) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      reject(new WorktreeError(opts.code || "GIT_ERROR", error.message));
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (code === 0) resolve(stdout);
      else reject(new WorktreeError(opts.code || "GIT_ERROR", (stderr || `git exited ${code}`).trim().slice(-2000)));
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

async function repositoryInfo(cwd) {
  const start = path.resolve(String(cwd || process.cwd()));
  let root, common;
  try {
    root = (await _git(start, ["rev-parse", "--show-toplevel"], { code: "NOT_GIT_REPOSITORY" })).trim();
    common = (await _git(start, ["rev-parse", "--path-format=absolute", "--git-common-dir"], { code: "NOT_GIT_REPOSITORY" })).trim();
  } catch (error) {
    if (error instanceof WorktreeError) _fail("NOT_GIT_REPOSITORY", "当前工作区不是 Git 仓库");
    throw error;
  }
  if (!root || !common) _fail("NOT_GIT_REPOSITORY", "当前工作区不是 Git 仓库");
  return { root: path.resolve(root), common: path.resolve(common) };
}

function parseWorktreePorcelain(raw) {
  const text = String(raw || "");
  const tokens = text.includes("\0") ? text.split("\0") : text.split(/\r?\n/);
  const result = [];
  let current = {};
  const flush = () => {
    if (current.worktree) result.push(current);
    current = {};
  };
  for (const token of tokens) {
    if (!token) { flush(); continue; }
    const space = token.indexOf(" ");
    const field = space < 0 ? token : token.slice(0, space);
    const value = space < 0 ? "" : token.slice(space + 1);
    if (field === "worktree") current.worktree = value;
    else if (field === "HEAD") current.head = value;
    else if (field === "branch") current.branch_ref = value;
    else if (field === "detached") current.detached = true;
    else if (field === "bare") current.bare = true;
    else if (field === "locked") { current.locked = true; current.lock_reason = value; }
    else if (field === "prunable") { current.prunable = true; current.prune_reason = value; }
  }
  flush();
  return result;
}

function _registryPath(managedRoot) { return path.join(managedRoot, "registry-v1.json"); }

function _readRegistry(managedRoot) {
  const root = path.resolve(managedRoot);
  let value = null;
  try { value = JSON.parse(fs.readFileSync(_registryPath(root), "utf8")); } catch (_e) { /* empty/corrupt -> safe empty */ }
  const items = value && Array.isArray(value.items) ? value.items : [];
  return {
    version: REGISTRY_VERSION,
    items: items.filter((item) => item && typeof item === "object" && /^[a-z0-9-]{6,80}$/i.test(String(item.id || ""))
      && item.path && _within(root, path.resolve(String(item.path)))),
  };
}

function _writeRegistry(managedRoot, registry) {
  const root = path.resolve(managedRoot);
  fs.mkdirSync(root, { recursive: true });
  const target = _registryPath(root);
  const temp = target + `.tmp-${process.pid}-${crypto.randomBytes(4).toString("hex")}`;
  const payload = JSON.stringify({ version: REGISTRY_VERSION, items: registry.items || [] }, null, 2) + "\n";
  let fd;
  try {
    fd = fs.openSync(temp, "wx", 0o600);
    fs.writeFileSync(fd, payload, "utf8");
    fs.fsyncSync(fd);
  } finally { if (fd !== undefined) fs.closeSync(fd); }
  try { fs.renameSync(temp, target); }
  catch (_e) {
    // Windows cannot always atomically replace an existing file. Registry data
    // is advisory; destructive operations still revalidate Git and ownership.
    try { fs.rmSync(target, { force: true }); } catch (_ignore) { /* */ }
    fs.renameSync(temp, target);
  }
}

function _repoRegistry(managedRoot, common) {
  const registry = _readRegistry(managedRoot);
  const commonKey = _key(common);
  return { registry, items: registry.items.filter((item) => _key(item.repo_common) === commonKey) };
}

function _branchName(branchRef) {
  const value = String(branchRef || "");
  return value.startsWith("refs/heads/") ? value.slice("refs/heads/".length) : value;
}

function _fileFingerprint(file) {
  const data = fs.readFileSync(file);
  return { bytes: data.length, sha256: crypto.createHash("sha256").update(data).digest("hex") };
}

async function _ignoredRiskOf(worktreePath, meta) {
  if (!meta) return { ignored_files: 0, ignored_risk_files: 0, ignored_risk_paths: [] };
  let raw = "";
  try { raw = await _git(worktreePath, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], { maxBuffer: 32 * 1024 * 1024 }); }
  catch (_e) { return { ignored_files: 0, ignored_risk_files: 1, ignored_risk_paths: ["(ignored 文件检查失败)"] }; }
  const ignored = raw.split("\0").filter(Boolean);
  const fingerprints = meta.copied_file_fingerprints && typeof meta.copied_file_fingerprints === "object"
    ? meta.copied_file_fingerprints : {};
  let riskCount = 0;
  const riskPaths = [];
  const markRisk = (rel) => { riskCount++; if (riskPaths.length < 20) riskPaths.push(rel); };
  for (const rawRel of ignored) {
    const rel = String(rawRel).replace(/\\/g, "/");
    const expected = fingerprints[rel];
    if (!expected) { markRisk(rel); continue; }
    const target = path.resolve(worktreePath, ...rel.split("/"));
    const source = meta.source_root ? path.resolve(meta.source_root, ...rel.split("/")) : "";
    try {
      const targetLst = fs.lstatSync(target);
      const sourceLst = fs.lstatSync(source);
      if (!targetLst.isFile() || targetLst.isSymbolicLink() || !sourceLst.isFile() || sourceLst.isSymbolicLink()) {
        markRisk(rel); continue;
      }
      const targetFp = _fileFingerprint(target);
      const sourceFp = _fileFingerprint(source);
      if (targetFp.bytes !== expected.bytes || targetFp.sha256 !== expected.sha256
          || sourceFp.bytes !== expected.bytes || sourceFp.sha256 !== expected.sha256) markRisk(rel);
    } catch (_e) { markRisk(rel); }
  }
  return { ignored_files: ignored.length, ignored_risk_files: riskCount, ignored_risk_paths: riskPaths };
}

async function _statusOf(worktreePath, meta) {
  try {
    const raw = await _git(worktreePath, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { timeout: 15000 });
    const records = raw.split("\0").filter(Boolean);
    const ignored = await _ignoredRiskOf(worktreePath, meta);
    return { dirty: records.length > 0 || ignored.ignored_risk_files > 0,
      changed_files: records.length, status: raw, ...ignored };
  } catch (error) {
    return { dirty: true, changed_files: 0, status: "", ignored_files: 0,
      ignored_risk_files: 1, ignored_risk_paths: ["(状态检查失败)"], status_error: error.message };
  }
}

async function listWorktrees(cwd, opts = {}) {
  const info = await repositoryInfo(cwd);
  const managedRoot = defaultManagedRoot(opts);
  fs.mkdirSync(managedRoot, { recursive: true });
  const parsed = parseWorktreePorcelain(await _git(info.root, ["worktree", "list", "--porcelain", "-z"]));
  const { items: registered } = _repoRegistry(managedRoot, info.common);
  const byPath = new Map(registered.map((item) => [_key(item.path), item]));
  const currentRoot = info.root;
  const activeKey = opts.activePath ? _key(_realIfExists(opts.activePath)) : "";
  const out = [];
  for (const item of parsed.slice(0, 100)) {
    const wtPath = path.resolve(item.worktree);
    const meta = byPath.get(_key(wtPath));
    const exists = fs.existsSync(wtPath);
    const state = exists && !item.bare ? await _statusOf(wtPath, meta) : { dirty: false, changed_files: 0, status: "", ignored_files: 0, ignored_risk_files: 0, ignored_risk_paths: [] };
    const ownedPath = _within(managedRoot, wtPath);
    out.push({
      id: meta ? meta.id : undefined,
      path: wtPath,
      head: item.head || "",
      branch: _branchName(item.branch_ref),
      detached: !!item.detached,
      bare: !!item.bare,
      locked: !!item.locked,
      lock_reason: item.lock_reason || "",
      prunable: !!item.prunable,
      exists,
      current: _key(wtPath) === _key(currentRoot),
      active: !!activeKey && _key(_realIfExists(wtPath)) === activeKey,
      managed: !!meta && ownedPath,
      permanent: !!(meta && meta.permanent),
      base_ref: meta ? meta.base_ref : undefined,
      base_commit: meta ? meta.base_commit : undefined,
      created_at: meta ? meta.created_at : undefined,
      last_used_at: meta ? meta.last_used_at : undefined,
      local_changes_applied: !!(meta && meta.local_changes_applied),
      copied_files: meta && Array.isArray(meta.copied_files) ? meta.copied_files.length : 0,
      dirty: state.dirty,
      changed_files: state.changed_files,
      ignored_files: state.ignored_files,
      ignored_risk_files: state.ignored_risk_files,
      ignored_risk_paths: state.ignored_risk_paths,
      status_error: state.status_error,
      unique_detached_commits: !!(meta && item.detached && item.head && meta.base_commit && item.head !== meta.base_commit),
    });
  }
  return { ok: true, repo_root: info.root, repo_common: info.common, managed_root: managedRoot,
    keep_limit: Math.max(1, Math.min(Number(opts.keep) || DEFAULT_KEEP, 100)),
    auto_cleanup: opts.autoCleanup === true, items: out };
}

function _safeRef(value) {
  const ref = String(value || "HEAD").trim();
  if (!ref || ref.length > 240 || ref.startsWith("-") || /[\0\r\n]/.test(ref)) _fail("INVALID_BASE_REF", "起始分支或提交格式不安全");
  return ref;
}

function _safeSlug(value) {
  const slug = String(value || "task").normalize("NFKC").replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 40);
  return slug || "task";
}

function _compileIncludePattern(input) {
  let value = String(input || "").trim();
  if (!value || value.startsWith("#")) return null;
  let negated = false;
  if (value.startsWith("!")) { negated = true; value = value.slice(1); }
  value = value.replace(/\\/g, "/");
  const anchored = value.startsWith("/");
  if (anchored) value = value.slice(1);
  const dirOnly = value.endsWith("/");
  if (dirOnly) value = value.slice(0, -1);
  if (!value || value.includes("\0") || value.split("/").includes("..")) return null;
  const hasSlash = value.includes("/");
  let body = "";
  for (let i = 0; i < value.length; i++) {
    const ch = value[i];
    if (ch === "*" && value[i + 1] === "*") {
      while (value[i + 1] === "*") i++;
      if (value[i + 1] === "/") { i++; body += "(?:.*/)?"; }
      else body += ".*";
    } else if (ch === "*") body += "[^/]*";
    else if (ch === "?") body += "[^/]";
    else body += ch.replace(/[|\\{}()[\]^$+?.]/g, "\\$&");
  }
  if (dirOnly) body += "(?:/.*)?";
  const prefix = anchored || hasSlash ? "^" : "^(?:.*/)?";
  return { negated, regex: new RegExp(prefix + body + "$") };
}

function matchWorktreeInclude(relativePath, lines) {
  const rel = String(relativePath || "").replace(/\\/g, "/").replace(/^\.\//, "");
  let matched = false;
  for (const line of lines || []) {
    const rule = typeof line === "string" ? _compileIncludePattern(line) : line;
    if (rule && rule.regex.test(rel)) matched = !rule.negated;
  }
  return matched;
}

async function _copyIncludedIgnoredFiles(sourceRoot, targetRoot) {
  const includePath = path.join(sourceRoot, ".worktreeinclude");
  let lines = [];
  try {
    const stat = fs.statSync(includePath);
    if (stat.size > MAX_INCLUDE_BYTES) _fail("WORKTREE_INCLUDE_TOO_LARGE", ".worktreeinclude 超过 64 KiB");
    lines = fs.readFileSync(includePath, "utf8").split(/\r?\n/).slice(0, 500).map(_compileIncludePattern).filter(Boolean);
  } catch (error) {
    if (error instanceof WorktreeError) throw error;
    lines = [];
  }
  const raw = await _git(sourceRoot, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], { maxBuffer: 32 * 1024 * 1024 });
  const ignored = raw.split("\0").filter(Boolean);
  const candidates = ignored.filter((rel) => path.basename(rel).toLowerCase() === "agents.override.md" || matchWorktreeInclude(rel, lines));
  if (candidates.length > MAX_COPY_FILES) _fail("WORKTREE_INCLUDE_TOO_MANY", `.worktreeinclude 匹配 ${candidates.length} 个文件，超过 ${MAX_COPY_FILES} 个上限`);
  const copied = [];
  const fingerprints = {};
  let total = 0;
  for (const rawRel of candidates) {
    const rel = String(rawRel).replace(/\\/g, "/");
    if (!rel || path.isAbsolute(rel) || rel.split("/").includes("..")) continue;
    const source = path.resolve(sourceRoot, ...rel.split("/"));
    const target = path.resolve(targetRoot, ...rel.split("/"));
    if (!_within(sourceRoot, source) || !_within(targetRoot, target)) continue;
    let lst, real;
    try { lst = fs.lstatSync(source); real = fs.realpathSync(source); } catch (_e) { continue; }
    if (!lst.isFile() || lst.isSymbolicLink() || !_within(sourceRoot, real)) continue;
    if (lst.size > MAX_COPY_FILE_BYTES) _fail("WORKTREE_INCLUDE_FILE_TOO_LARGE", `${rel} 超过 10 MiB，不复制`);
    total += lst.size;
    if (total > MAX_COPY_TOTAL_BYTES) _fail("WORKTREE_INCLUDE_TOTAL_TOO_LARGE", ".worktreeinclude 复制总量超过 50 MiB");
    if (fs.existsSync(target)) continue;
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
    try { fs.chmodSync(target, lst.mode & 0o777); } catch (_e) { /* Windows */ }
    copied.push(rel);
    fingerprints[rel] = _fileFingerprint(source);
  }
  return { files: copied, fingerprints };
}

async function _cleanupFailedWorktree(repoRoot, managedRoot, target) {
  if (!_within(managedRoot, target)) return;
  try { await _git(repoRoot, ["worktree", "remove", "--force", "--", target], { timeout: 30000 }); } catch (_e) { /* source still owns all transferred data */ }
  try { fs.rmSync(target, { recursive: true, force: true }); } catch (_e) { /* */ }
  try { await _git(repoRoot, ["worktree", "prune"]); } catch (_e) { /* */ }
}

async function createWorktree(cwd, options = {}, opts = {}) {
  const info = await repositoryInfo(cwd);
  const managedRoot = defaultManagedRoot(opts);
  fs.mkdirSync(managedRoot, { recursive: true });
  const realManagedRoot = _realIfExists(managedRoot);
  const baseRef = _safeRef(options.base_ref || "HEAD");
  let baseCommit;
  try { baseCommit = (await _git(info.root, ["rev-parse", "--verify", `${baseRef}^{commit}`], { code: "INVALID_BASE_REF" })).trim(); }
  catch (_e) { _fail("INVALID_BASE_REF", `找不到起始分支或提交：${baseRef}`); }
  const sourceHead = (await _git(info.root, ["rev-parse", "HEAD"])).trim();
  let patch = "";
  if (options.include_local_changes) {
    if (baseCommit !== sourceHead) _fail("LOCAL_CHANGES_BASE_MISMATCH", "复制当前改动时，起始点必须是当前 HEAD");
    patch = await _git(info.root, ["diff", "--binary", "HEAD", "--", "."], { maxBuffer: MAX_PATCH_BYTES + 1024 });
    if (Buffer.byteLength(patch, "utf8") > MAX_PATCH_BYTES) _fail("LOCAL_PATCH_TOO_LARGE", "当前 tracked 改动超过 20 MiB，拒绝自动复制");
  }
  const repoKey = crypto.createHash("sha256").update(_key(info.common)).digest("hex").slice(0, 12);
  const id = `${Date.now().toString(36)}-${crypto.randomBytes(5).toString("hex")}`;
  const label = _safeSlug(options.label || path.basename(info.root));
  let parent = path.join(realManagedRoot, repoKey);
  fs.mkdirSync(parent, { recursive: true });
  parent = _realIfExists(parent);
  if (!_within(realManagedRoot, parent)) _fail("MANAGED_ROOT_ESCAPE", "托管 worktree 父目录越过 HashMM 数据边界");
  const target = path.join(parent, `${label}-${id}`);
  if (!_within(realManagedRoot, target) || fs.existsSync(target)) _fail("MANAGED_PATH_COLLISION", "无法生成安全的 worktree 目录");

  let copiedFiles = [];
  let copiedFileFingerprints = {};
  try {
    await _git(info.root, ["worktree", "add", "--detach", "--", target, baseCommit], { timeout: 120000, code: "WORKTREE_CREATE_FAILED" });
    if (patch.trim()) await _gitInput(target, ["apply", "--whitespace=nowarn", "--recount", "-"], patch, { timeout: 120000, code: "LOCAL_PATCH_APPLY_FAILED" });
    if (options.copy_ignored !== false) {
      const copied = await _copyIncludedIgnoredFiles(info.root, target);
      copiedFiles = copied.files;
      copiedFileFingerprints = copied.fingerprints;
    }
  } catch (error) {
    await _cleanupFailedWorktree(info.root, realManagedRoot, target);
    throw error;
  }

  const now = new Date().toISOString();
  const registry = _readRegistry(realManagedRoot);
  const entry = {
    id, path: target, repo_common: info.common, source_root: info.root,
    base_ref: baseRef, base_commit: baseCommit, created_at: now, last_used_at: now,
    permanent: !!options.permanent, local_changes_applied: !!(options.include_local_changes && patch.trim()),
    copied_files: copiedFiles, copied_file_fingerprints: copiedFileFingerprints,
  };
  registry.items.push(entry);
  _writeRegistry(realManagedRoot, registry);
  const cleanup = opts.autoCleanup !== false
    ? await cleanupManagedWorktrees(info.root, { keep: opts.keep || DEFAULT_KEEP, activePath: target }, { ...opts, managedRoot: realManagedRoot })
    : { removed: [] };
  return {
    ok: true, id, path: target, base_ref: baseRef, base_commit: baseCommit,
    created_at: now, permanent: entry.permanent, detached: true, head: baseCommit,
    local_changes_applied: entry.local_changes_applied, copied_files: copiedFiles,
    cleanup_removed: cleanup.removed || [],
  };
}

async function _managedItem(cwd, targetPath, opts = {}) {
  const info = await repositoryInfo(cwd);
  const managedRoot = defaultManagedRoot(opts);
  const target = path.resolve(String(targetPath || ""));
  if (!_within(managedRoot, target)) _fail("NOT_MANAGED_WORKTREE", "目标不在 HashMM 托管 worktree 根目录内");
  const registry = _readRegistry(managedRoot);
  const meta = registry.items.find((item) => _key(item.path) === _key(target) && _key(item.repo_common) === _key(info.common));
  if (!meta) _fail("NOT_MANAGED_WORKTREE", "目标不是 HashMM 登记的 worktree");
  const listed = await listWorktrees(info.root, { ...opts, managedRoot });
  const item = listed.items.find((value) => _key(value.path) === _key(target));
  if (!item || !item.exists) _fail("WORKTREE_MISSING", "worktree 已不存在，请先执行 Git prune");
  return { info, managedRoot, registry, meta, item, target };
}

async function validateWorktreeTarget(cwd, targetPath, opts = {}) {
  const listed = await listWorktrees(cwd, opts);
  const target = path.resolve(String(targetPath || ""));
  const item = listed.items.find((value) => _key(value.path) === _key(target));
  if (!item || !item.exists || item.bare) _fail("UNKNOWN_WORKTREE", "目标不是当前仓库的可用 worktree");
  return { ...item, repo_root: listed.repo_root, managed_root: listed.managed_root };
}

async function touchManagedWorktree(cwd, targetPath, opts = {}) {
  const valid = await validateWorktreeTarget(cwd, targetPath, opts);
  if (!valid.managed) return valid;
  const managedRoot = defaultManagedRoot(opts);
  const registry = _readRegistry(managedRoot);
  const entry = registry.items.find((item) => _key(item.path) === _key(valid.path));
  if (entry) { entry.last_used_at = new Date().toISOString(); _writeRegistry(managedRoot, registry); }
  return valid;
}

async function createBranch(cwd, targetPath, branchName, opts = {}) {
  const ctx = await _managedItem(cwd, targetPath, opts);
  if (!ctx.item.detached) _fail("WORKTREE_ALREADY_ON_BRANCH", `worktree 已在分支 ${ctx.item.branch || "(未知)"}`);
  const branch = String(branchName || "").trim();
  if (!branch || branch.length > 240 || branch.startsWith("-") || /[\0\r\n]/.test(branch)) _fail("INVALID_BRANCH_NAME", "分支名格式不安全");
  try { await _git(ctx.info.root, ["check-ref-format", "--branch", branch], { code: "INVALID_BRANCH_NAME" }); }
  catch (_e) { _fail("INVALID_BRANCH_NAME", `无效分支名：${branch}`); }
  await _git(ctx.target, ["switch", "-c", branch], { timeout: 60000, code: "CREATE_BRANCH_FAILED" });
  ctx.meta.branch_created = branch;
  ctx.meta.last_used_at = new Date().toISOString();
  _writeRegistry(ctx.managedRoot, ctx.registry);
  return { ok: true, path: ctx.target, branch };
}

async function setPermanent(cwd, targetPath, permanent, opts = {}) {
  const ctx = await _managedItem(cwd, targetPath, opts);
  ctx.meta.permanent = !!permanent;
  ctx.meta.last_used_at = new Date().toISOString();
  _writeRegistry(ctx.managedRoot, ctx.registry);
  return { ok: true, path: ctx.target, permanent: ctx.meta.permanent };
}

async function removeWorktree(cwd, targetPath, options = {}, opts = {}) {
  const ctx = await _managedItem(cwd, targetPath, opts);
  if (!options.expected_id || options.expected_id !== ctx.meta.id) _fail("CONFIRMATION_MISMATCH", "删除确认已过期，请刷新后重试");
  if (ctx.meta.permanent) _fail("PERMANENT_WORKTREE", "永久 worktree 不会被删除，请先取消永久标记");
  if (options.active_path && _key(_realIfExists(options.active_path)) === _key(_realIfExists(ctx.target))) _fail("ACTIVE_WORKTREE", "当前正在使用此 worktree，请先切换到其他工作区");
  if (ctx.item.locked) _fail("LOCKED_WORKTREE", "worktree 已被 Git 锁定");
  if (ctx.item.ignored_risk_files) {
    _fail("IGNORED_WORKTREE_FILES", `worktree 有 ${ctx.item.ignored_risk_files} 个 ignored 文件没有可验证的同哈希源副本，拒绝删除`);
  }
  if (ctx.item.dirty) _fail("DIRTY_WORKTREE", `worktree 有 ${ctx.item.changed_files} 个未提交变更，拒绝删除`);
  if (ctx.item.unique_detached_commits) _fail("UNANCHORED_COMMITS", "detached worktree 含独有提交，请先“在此创建分支”保存提交");
  await _git(ctx.info.root, ["worktree", "remove", "--", ctx.target], { timeout: 120000, code: "WORKTREE_REMOVE_FAILED" });
  ctx.registry.items = ctx.registry.items.filter((item) => item.id !== ctx.meta.id);
  _writeRegistry(ctx.managedRoot, ctx.registry);
  try { await _git(ctx.info.root, ["worktree", "prune"]); } catch (_e) { /* */ }
  return { ok: true, removed: ctx.target, id: ctx.meta.id };
}

async function cleanupManagedWorktrees(cwd, options = {}, opts = {}) {
  const keep = Math.max(1, Math.min(Number(options.keep) || DEFAULT_KEEP, 100));
  const listed = await listWorktrees(cwd, { ...opts, activePath: options.activePath });
  const managed = listed.items.filter((item) => item.managed).sort((a, b) =>
    String(b.last_used_at || b.created_at || "").localeCompare(String(a.last_used_at || a.created_at || "")));
  const removed = [];
  for (const item of managed.slice(keep)) {
    // Automatic cleanup is intentionally narrower than manual cleanup: only
    // untouched detached worktrees have no user work to snapshot or anchor.
    if (item.permanent || item.active || item.dirty || item.locked || !item.detached || item.unique_detached_commits || !item.id) continue;
    try {
      const result = await removeWorktree(listed.repo_root, item.path,
        { expected_id: item.id, active_path: options.activePath }, { ...opts, managedRoot: listed.managed_root });
      removed.push(result.removed);
    } catch (_e) { /* cleanup is best-effort and never broadens removal */ }
  }
  return { ok: true, keep, removed };
}

module.exports = {
  REGISTRY_VERSION, DEFAULT_KEEP, MAX_PATCH_BYTES, MAX_COPY_FILES, MAX_COPY_TOTAL_BYTES,
  WorktreeError, defaultManagedRoot, repositoryInfo, parseWorktreePorcelain,
  matchWorktreeInclude, listWorktrees, createWorktree, validateWorktreeTarget,
  touchManagedWorktree, createBranch, setPermanent, removeWorktree, cleanupManagedWorktrees,
  _within, _compileIncludePattern, _readRegistry,
};
