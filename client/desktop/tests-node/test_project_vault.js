"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  MARKER_NAME,
  buildManifest,
  ensureProjectVault,
  resolveProjectVault,
} = require("../services/project-vault");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-vault-test-"));
try {
  const install = path.join(root, "install");
  const legacy = path.join(install, "local-backend", "data");
  const vault = path.join(install, "HashMM Data");
  fs.mkdirSync(path.join(legacy, "conversations", "c1"), { recursive: true });
  fs.writeFileSync(path.join(legacy, "hashmm.sqlite"), "database-v706", "utf8");
  fs.writeFileSync(path.join(legacy, "conversations", "c1", "paper.pdf"), "paper", "utf8");

  assert.strictEqual(resolveProjectVault({ installDir: install }), vault);
  assert.strictEqual(resolveProjectVault({ configuredDir: path.join(root, "custom"), installDir: install }),
    path.join(root, "custom"));

  const result = ensureProjectVault({ vaultDir: vault, legacyDataDirs: [legacy] });
  assert.strictEqual(result.migrated, true);
  assert.strictEqual(result.source, path.resolve(legacy));
  assert(fs.existsSync(path.join(vault, MARKER_NAME)), "迁移回执必须存在");
  assert(fs.existsSync(path.join(legacy, "hashmm.sqlite")), "旧数据必须保留");
  assert.deepStrictEqual(buildManifest(vault), buildManifest(legacy), "迁移前后文件清单和 SHA-256 必须一致");
  const marker = JSON.parse(fs.readFileSync(path.join(vault, MARKER_NAME), "utf8"));
  assert.strictEqual(marker.source_retained, true);
  assert.strictEqual(marker.file_count, 2);

  fs.writeFileSync(path.join(legacy, "late-change.txt"), "do-not-overwrite-vault", "utf8");
  const second = ensureProjectVault({ vaultDir: vault, legacyDataDirs: [legacy] });
  assert.strictEqual(second.migrated, false, "重复启动不能再次覆盖 ProjectVault");
  assert(!fs.existsSync(path.join(vault, "late-change.txt")));

  const fresh = path.join(root, "fresh", "HashMM Data");
  const freshResult = ensureProjectVault({ vaultDir: fresh, legacyDataDirs: [] });
  assert.strictEqual(freshResult.migrated, false);
  assert(fs.existsSync(path.join(fresh, MARKER_NAME)));

  console.log("project-vault: 迁移校验、旧数据保留、幂等与全新初始化全部通过");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
