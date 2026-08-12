/**
 * ProjectVault owns durable HashMM user data.  Runtime dependencies stay in
 * <install>/local-backend; databases, conversations, indexes and backups live
 * in <install>/HashMM Data (or an explicitly selected directory).
 *
 * Legacy migration is deliberately copy-only: the old directory is retained
 * until the user removes it.  A manifest is verified before the staging
 * directory is atomically promoted.
 */
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const VAULT_DIR_NAME = "HashMM Data";
const MARKER_NAME = ".hashmm-vault.json";

function resolveProjectVault({ configuredDir, installDir }) {
  const configured = String(configuredDir || "").trim();
  if (configured) return path.resolve(configured);
  if (!installDir) throw new Error("无法确定 HashMM 安装目录");
  return path.join(path.resolve(String(installDir)), VAULT_DIR_NAME);
}

function inspectProjectVaultStatus({ desiredDir, defaultDir, configuredDir, localStatus = {}, fsApi = fs }) {
  const desired = path.resolve(String(desiredDir || ""));
  const fallback = path.resolve(String(defaultDir || ""));
  const normalize = (value) => path.resolve(String(value || "")).replace(/[\\/]+$/, "").toLowerCase();
  let exists = false;
  let writable = false;
  let availability = "not_initialized";
  let error = "";
  try {
    exists = fsApi.existsSync(desired);
    if (exists) {
      fsApi.accessSync(desired, fs.constants.R_OK | fs.constants.W_OK);
      writable = true;
      availability = "ready";
    }
  } catch (exc) {
    availability = "permission_denied";
    error = String(exc && exc.message || exc);
  }
  const running = !!localStatus.running;
  const effectiveDir = running && localStatus.dataDir ? path.resolve(String(localStatus.dataDir)) : null;
  return {
    ok: true,
    schema: "hashmm.project-vault-status.v1",
    dataDir: desired,
    desiredDir: desired,
    effectiveDir,
    source: String(configuredDir || "").trim() ? "configured" : "installation_default",
    isDefault: normalize(desired) === normalize(fallback),
    defaultDir: fallback,
    exists,
    writable,
    availability,
    localBackendRunning: running,
    restartRequired: running && !!effectiveDir && normalize(effectiveDir) !== normalize(desired),
    error,
  };
}

function _inside(parent, child) {
  const rel = path.relative(path.resolve(parent), path.resolve(child));
  return rel !== "" && !rel.startsWith(".." + path.sep) && rel !== ".." && !path.isAbsolute(rel);
}

function _files(root) {
  const result = [];
  if (!fs.existsSync(root)) return result;
  const visit = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const absolute = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`ProjectVault 不迁移符号链接：${absolute}`);
      if (entry.isDirectory()) visit(absolute);
      else if (entry.isFile()) result.push(absolute);
    }
  };
  visit(root);
  return result.sort((a, b) => a.localeCompare(b));
}

function _sha256(file) {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(file));
  return hash.digest("hex");
}

function buildManifest(root) {
  const base = path.resolve(root);
  const files = _files(base).filter((file) => path.basename(file) !== MARKER_NAME);
  return files.map((file) => {
    const stat = fs.statSync(file);
    return {
      path: path.relative(base, file).split(path.sep).join("/"),
      size: stat.size,
      sha256: _sha256(file),
    };
  });
}

function _sameManifest(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function _copyTree(source, destination) {
  fs.mkdirSync(destination, { recursive: true });
  for (const sourceFile of _files(source)) {
    const relative = path.relative(source, sourceFile);
    const destinationFile = path.join(destination, relative);
    if (!_inside(destination, destinationFile)) throw new Error("ProjectVault 迁移路径越界");
    fs.mkdirSync(path.dirname(destinationFile), { recursive: true });
    fs.copyFileSync(sourceFile, destinationFile, fs.constants.COPYFILE_EXCL);
  }
}

function _writeMarker(vaultDir, details) {
  const marker = {
    schema_version: 1,
    kind: "hashmm-project-vault",
    created_at: new Date().toISOString(),
    ...details,
  };
  fs.writeFileSync(path.join(vaultDir, MARKER_NAME), JSON.stringify(marker, null, 2) + "\n", "utf8");
  return marker;
}

/**
 * Ensure the vault exists and, on first use, copy the first non-empty legacy
 * data directory into it.  The legacy source is never deleted or renamed.
 */
function ensureProjectVault({ vaultDir, legacyDataDirs = [] }) {
  const destination = path.resolve(vaultDir);
  const parent = path.dirname(destination);
  fs.mkdirSync(parent, { recursive: true });

  if (fs.existsSync(destination)) {
    if (!fs.statSync(destination).isDirectory()) throw new Error(`ProjectVault 不是目录：${destination}`);
    if (!fs.existsSync(path.join(destination, MARKER_NAME))) {
      _writeMarker(destination, { migration: "adopted-existing", source: null });
    }
    return { ok: true, vaultDir: destination, migrated: false, source: null };
  }

  const candidates = legacyDataDirs
    .map((item) => path.resolve(String(item || "")))
    .filter((item, index, all) => item && item !== destination && all.indexOf(item) === index)
    .filter((item) => fs.existsSync(item) && fs.statSync(item).isDirectory() && _files(item).length > 0);
  const source = candidates[0] || null;

  if (!source) {
    fs.mkdirSync(destination, { recursive: false });
    _writeMarker(destination, { migration: "fresh", source: null });
    return { ok: true, vaultDir: destination, migrated: false, source: null };
  }

  const staging = path.join(parent, `.${path.basename(destination)}.migrating-${process.pid}-${Date.now()}`);
  if (!_inside(parent, staging)) throw new Error("ProjectVault 暂存路径越界");
  try {
    _copyTree(source, staging);
    const sourceManifest = buildManifest(source);
    const stagedManifest = buildManifest(staging);
    if (!_sameManifest(sourceManifest, stagedManifest)) throw new Error("ProjectVault 迁移清单校验失败");
    _writeMarker(staging, {
      migration: "copied-and-verified",
      source,
      file_count: stagedManifest.length,
      total_bytes: stagedManifest.reduce((sum, item) => sum + item.size, 0),
      source_retained: true,
    });
    fs.renameSync(staging, destination);
    return { ok: true, vaultDir: destination, migrated: true, source };
  } catch (error) {
    if (fs.existsSync(staging) && _inside(parent, staging)) fs.rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

module.exports = {
  MARKER_NAME,
  VAULT_DIR_NAME,
  buildManifest,
  ensureProjectVault,
  inspectProjectVaultStatus,
  resolveProjectVault,
};
