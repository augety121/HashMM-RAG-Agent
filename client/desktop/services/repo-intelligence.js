/**
 * Repository context for the desktop workbench (V336).
 *
 * Mirrors the useful parts of Codex repository discovery without trusting
 * shell interpolation: git is always executed through execFile, instruction
 * files are resolved through real paths, and project guidance is capped at
 * 32 KiB per run.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");

const MAX_INSTRUCTION_BYTES = 32 * 1024;
const MAX_PLAN_BYTES = 16 * 1024;
const MAX_DIFF_BYTES = 350 * 1024;
const DEFAULT_FALLBACKS = ["TEAM_GUIDE.md", ".agents.md"];

function _within(root, candidate) {
  const rel = path.relative(path.resolve(root), path.resolve(candidate));
  return rel === "" || (!rel.startsWith(".." + path.sep) && rel !== ".." && !path.isAbsolute(rel));
}

function _safeRealFile(root, candidate, fsImpl = fs) {
  try {
    const lst = fsImpl.lstatSync(candidate);
    if (!lst.isFile() && !lst.isSymbolicLink()) return null;
    const realRoot = fsImpl.realpathSync(root);
    const realFile = fsImpl.realpathSync(candidate);
    if (!_within(realRoot, realFile)) return null;
    if (!fsImpl.statSync(realFile).isFile()) return null;
    return realFile;
  } catch (_e) { return null; }
}

function _readCapped(file, remaining, fsImpl = fs) {
  if (!file || remaining <= 0) return { content: "", bytes: 0, truncated: true };
  const raw = fsImpl.readFileSync(file);
  if (!raw.length) return { content: "", bytes: 0, truncated: false };
  const take = Math.min(raw.length, remaining);
  return { content: raw.subarray(0, take).toString("utf8"), bytes: take, truncated: raw.length > take };
}

function _candidateAt(dir, fallbacks, fsImpl = fs) {
  for (const name of ["AGENTS.override.md", "AGENTS.md", ...(fallbacks || [])]) {
    const candidate = path.join(dir, name);
    try {
      if (fsImpl.existsSync(candidate) && fsImpl.statSync(candidate).size > 0) return candidate;
    } catch (_e) { /* keep looking */ }
  }
  return null;
}

function _directoryChain(root, cwd) {
  const rr = path.resolve(root);
  const cc = path.resolve(cwd);
  if (!_within(rr, cc)) return [rr];
  const rel = path.relative(rr, cc);
  const out = [rr];
  if (!rel) return out;
  let cur = rr;
  for (const part of rel.split(path.sep).filter(Boolean)) {
    cur = path.join(cur, part);
    out.push(cur);
  }
  return out;
}

function discoverInstructions({ repoRoot, cwd, homeDir, fallbacks, maxBytes, fsImpl } = {}) {
  const fsi = fsImpl || fs;
  const root = path.resolve(repoRoot || cwd || process.cwd());
  const current = path.resolve(cwd || root);
  const home = path.resolve(homeDir || os.homedir());
  const names = Array.isArray(fallbacks) ? fallbacks.filter(Boolean).slice(0, 10) : DEFAULT_FALLBACKS;
  const cap = Math.max(1024, Math.min(Number(maxBytes) || MAX_INSTRUCTION_BYTES, 256 * 1024));
  const sources = [];
  let used = 0;
  let truncated = false;

  // HashMM keeps its own global guidance separate from Codex configuration.
  const globalDir = path.join(home, ".hashmm");
  const globalCandidate = _candidateAt(globalDir, [], fsi);
  if (globalCandidate) {
    const real = _safeRealFile(globalDir, globalCandidate, fsi);
    if (real) {
      const read = _readCapped(real, cap - used, fsi);
      if (read.content.trim()) {
        sources.push({ path: real, relative: path.basename(real), scope: "global", bytes: read.bytes, content: read.content });
        used += read.bytes; truncated = truncated || read.truncated;
      }
    }
  }

  for (const dir of _directoryChain(root, current)) {
    if (used >= cap) { truncated = true; break; }
    const candidate = _candidateAt(dir, names, fsi);
    if (!candidate) continue;
    const real = _safeRealFile(root, candidate, fsi);
    if (!real) continue;
    const read = _readCapped(real, cap - used, fsi);
    if (!read.content.trim()) continue;
    sources.push({
      path: real,
      relative: path.relative(root, real) || path.basename(real),
      scope: "project",
      bytes: read.bytes,
      content: read.content,
    });
    used += read.bytes;
    truncated = truncated || read.truncated;
  }
  return {
    sources,
    combined: sources.map((item) => `# Source: ${item.relative}\n${item.content.trim()}`).join("\n\n"),
    bytes: used,
    max_bytes: cap,
    truncated,
  };
}

function discoverPlanTemplate(repoRoot, cwd, fsImpl = fs) {
  const root = path.resolve(repoRoot || cwd || process.cwd());
  const dirs = _directoryChain(root, cwd || root).reverse();
  for (const dir of dirs) {
    const candidate = path.join(dir, "PLANS.md");
    const real = _safeRealFile(root, candidate, fsImpl);
    if (!real) continue;
    const read = _readCapped(real, MAX_PLAN_BYTES, fsImpl);
    if (read.content.trim()) {
      return { path: real, relative: path.relative(root, real) || "PLANS.md", content: read.content,
        bytes: read.bytes, truncated: read.truncated };
    }
  }
  return null;
}

function parsePorcelainZ(raw) {
  const parts = String(raw || "").split("\0");
  const files = [];
  for (let i = 0; i < parts.length; i++) {
    const rec = parts[i];
    if (!rec || rec.length < 4) continue;
    const x = rec[0], y = rec[1];
    let file = rec.slice(3);
    let original = "";
    if ((x === "R" || x === "C" || y === "R" || y === "C") && i + 1 < parts.length) {
      original = parts[++i] || "";
    }
    files.push({ path: file, original_path: original || undefined, index: x, worktree: y,
      untracked: x === "?" && y === "?", conflicted: x === "U" || y === "U" || (x === "A" && y === "A") || (x === "D" && y === "D") });
  }
  return files;
}

function splitUnifiedDiff(raw) {
  const text = String(raw || "");
  if (!text.trim()) return [];
  const starts = [];
  const re = /^diff --git /gm;
  let match;
  while ((match = re.exec(text))) starts.push(match.index);
  if (!starts.length) return [{ file: "changes", diff: text }];
  const chunks = [];
  for (let i = 0; i < starts.length; i++) {
    const chunk = text.slice(starts[i], starts[i + 1] || text.length);
    const fm = chunk.match(/^diff --git a\/(.+?) b\/(.+)$/m);
    chunks.push({ file: fm ? fm[2] : `change-${i + 1}`, diff: chunk });
  }
  return chunks;
}

function _execGit(cwd, args, maxBuffer = 16 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    execFile("git", ["-C", cwd, ...args], { timeout: 20000, maxBuffer, windowsHide: true },
      (err, stdout, stderr) => err ? reject(new Error(String(stderr || err.message || "git failed").trim())) : resolve(String(stdout || "")));
  });
}

function _capUtf8(text, cap) {
  const buf = Buffer.from(String(text || ""), "utf8");
  if (buf.length <= cap) return { text: buf.toString("utf8"), truncated: false, bytes: buf.length };
  return { text: buf.subarray(0, cap).toString("utf8"), truncated: true, bytes: cap };
}

async function inspectRepository(cwd, opts = {}) {
  const dir = path.resolve(String(cwd || process.cwd()));
  const st = fs.statSync(dir);
  if (!st.isDirectory()) throw new Error("工作区不是目录");
  let root = "";
  try { root = (await _execGit(dir, ["rev-parse", "--show-toplevel"])).trim(); } catch (_e) { root = ""; }
  // Match Codex discovery semantics: without a project/Git root, still load
  // guidance from the current directory instead of losing durable instructions.
  if (!root) {
    const instructions = discoverInstructions({ repoRoot: dir, cwd: dir, homeDir: opts.homeDir,
      fallbacks: opts.fallbacks, maxBytes: opts.maxInstructionBytes });
    return {
      ok: true, is_git: false, cwd: dir, root: dir, branch: "(非 Git 工作区)", head: "", files: [],
      unstaged: { text: "", truncated: false, bytes: 0, chunks: [] },
      staged: { text: "", truncated: false, bytes: 0, chunks: [] },
      instructions, plan: discoverPlanTemplate(dir, dir),
    };
  }
  const branch = (await _execGit(root, ["branch", "--show-current"])).trim() || "(detached HEAD)";
  const [statusRaw, unstagedRaw, stagedRaw, head] = await Promise.all([
    _execGit(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]),
    _execGit(root, ["diff", "--no-ext-diff", "--unified=3", "--", "."]),
    _execGit(root, ["diff", "--cached", "--no-ext-diff", "--unified=3", "--", "."]),
    _execGit(root, ["rev-parse", "HEAD"]).catch(() => ""),
  ]);
  const unstaged = _capUtf8(unstagedRaw, opts.maxDiffBytes || MAX_DIFF_BYTES);
  const staged = _capUtf8(stagedRaw, opts.maxDiffBytes || MAX_DIFF_BYTES);
  const instructions = discoverInstructions({ repoRoot: root, cwd: dir, homeDir: opts.homeDir,
    fallbacks: opts.fallbacks, maxBytes: opts.maxInstructionBytes });
  const plan = discoverPlanTemplate(root, dir);
  return {
    ok: true, is_git: true, cwd: dir, root, branch, head: String(head || "").trim(),
    files: parsePorcelainZ(statusRaw),
    unstaged: { ...unstaged, chunks: splitUnifiedDiff(unstaged.text) },
    staged: { ...staged, chunks: splitUnifiedDiff(staged.text) },
    instructions,
    plan,
  };
}

module.exports = {
  MAX_INSTRUCTION_BYTES, MAX_DIFF_BYTES, DEFAULT_FALLBACKS,
  discoverInstructions, discoverPlanTemplate, parsePorcelainZ, splitUnifiedDiff, inspectRepository,
  _within, _safeRealFile,
};
