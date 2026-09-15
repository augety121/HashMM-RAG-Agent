"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const WT = require("../services/worktree-manager");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-worktree-"));
const repo = path.join(tmp, "repo");
const managedRoot = path.join(tmp, "managed");
let passed = 0;

async function ok(name, fn) {
  await fn();
  passed++;
  console.log("  ✔ " + name);
}

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { windowsHide: true, encoding: "utf8" });
}

async function expectCode(promise, code) {
  await assert.rejects(promise, (error) => error && error.code === code,
    `应拒绝为 ${code}`);
}

async function main() {
try {
  fs.mkdirSync(repo, { recursive: true });
  git(repo, "init", "--quiet");
  git(repo, "config", "user.email", "hashmm-worktree@example.invalid");
  git(repo, "config", "user.name", "HashMM Worktree Test");
  git(repo, "config", "core.autocrlf", "false");
  fs.writeFileSync(path.join(repo, ".gitignore"), ".env\nAGENTS.override.md\nunlisted.tmp\n", "utf8");
  fs.writeFileSync(path.join(repo, ".worktreeinclude"), ".env\n", "utf8");
  fs.writeFileSync(path.join(repo, "tracked.txt"), "before\n", "utf8");
  git(repo, "add", ".gitignore", ".worktreeinclude", "tracked.txt");
  git(repo, "commit", "--quiet", "-m", "fixture");

  await ok("worktree porcelain -z 解析 detached、branch 与记录边界", async () => {
    const rows = WT.parseWorktreePorcelain("worktree C:/a\0HEAD abc\0detached\0\0worktree C:/b\0HEAD def\0branch refs/heads/feature/x\0\0");
    assert.strictEqual(rows.length, 2);
    assert.strictEqual(rows[0].detached, true);
    assert.strictEqual(rows[1].branch_ref, "refs/heads/feature/x");
  });

  await ok(".worktreeinclude 支持根路径、basename、glob 与后置排除", async () => {
    assert.strictEqual(WT.matchWorktreeInclude("nested/.env", [".env"]), true);
    assert.strictEqual(WT.matchWorktreeInclude("config/dev/a.json", ["config/**", "!config/dev/**"]), false);
    assert.strictEqual(WT.matchWorktreeInclude("config/prod/a.json", ["config/**", "!config/dev/**"]), true);
    assert.strictEqual(WT.matchWorktreeInclude("elsewhere/config/a.json", ["/config/**"]), false);
  });

  const first = await WT.createWorktree(repo, { label: "first", copy_ignored: false }, { managedRoot, keep: 15 });
  await ok("创建托管 worktree 默认 detached 且路径只能位于托管根", async () => {
    assert.strictEqual(first.ok, true);
    assert.strictEqual(first.detached, true);
    assert.strictEqual(WT._within(managedRoot, first.path), true);
    assert.strictEqual(fs.readFileSync(path.join(first.path, "tracked.txt"), "utf8"), "before\n");
  });

  await ok("枚举返回 HEAD、当前/活动、托管登记和干净状态", async () => {
    const listed = await WT.listWorktrees(repo, { managedRoot, activePath: first.path });
    const item = listed.items.find((value) => value.id === first.id);
    assert(item && item.managed && item.detached && item.active && !item.dirty);
    assert(/^[0-9a-f]{40}$/i.test(item.head));
  });

  fs.writeFileSync(path.join(first.path, "tracked.txt"), "dirty\n", "utf8");
  await ok("有未提交改动时 fail closed，绝不 force 删除", async () => {
    await expectCode(WT.removeWorktree(repo, first.path,
      { expected_id: first.id }, { managedRoot }), "DIRTY_WORKTREE");
    assert.strictEqual(fs.existsSync(first.path), true);
  });
  fs.writeFileSync(path.join(first.path, "tracked.txt"), "before\n", "utf8");

  await ok("detached worktree 可显式创建独立分支", async () => {
    const result = await WT.createBranch(repo, first.path, "feature/hashmm-worktree-test", { managedRoot });
    assert.strictEqual(result.branch, "feature/hashmm-worktree-test");
    const listed = await WT.listWorktrees(repo, { managedRoot });
    const item = listed.items.find((value) => value.id === first.id);
    assert(item && item.branch === "feature/hashmm-worktree-test" && !item.detached);
  });

  await ok("永久标记阻止回收，取消后精确 expected_id 才可删除", async () => {
    await WT.setPermanent(repo, first.path, true, { managedRoot });
    await expectCode(WT.removeWorktree(repo, first.path,
      { expected_id: first.id }, { managedRoot }), "PERMANENT_WORKTREE");
    await WT.setPermanent(repo, first.path, false, { managedRoot });
    await expectCode(WT.removeWorktree(repo, first.path,
      { expected_id: "stale-id" }, { managedRoot }), "CONFIRMATION_MISMATCH");
    const removed = await WT.removeWorktree(repo, first.path,
      { expected_id: first.id }, { managedRoot });
    assert.strictEqual(removed.ok, true);
    assert.strictEqual(fs.existsSync(first.path), false);
  });

  fs.writeFileSync(path.join(repo, "tracked.txt"), "after\n", "utf8");
  fs.writeFileSync(path.join(repo, ".env"), "TOKEN=test-only\n", "utf8");
  fs.writeFileSync(path.join(repo, "AGENTS.override.md"), "local override\n", "utf8");
  fs.writeFileSync(path.join(repo, "unlisted.tmp"), "do not copy\n", "utf8");
  const copied = await WT.createWorktree(repo, {
    label: "with-local", include_local_changes: true, copy_ignored: true,
  }, { managedRoot, keep: 15 });
  await ok("显式复制 tracked 改动，并只复制 include 匹配与 AGENTS.override.md", async () => {
    assert.strictEqual(fs.readFileSync(path.join(copied.path, "tracked.txt"), "utf8"), "after\n");
    assert.strictEqual(fs.readFileSync(path.join(copied.path, ".env"), "utf8"), "TOKEN=test-only\n");
    assert.strictEqual(fs.readFileSync(path.join(copied.path, "AGENTS.override.md"), "utf8"), "local override\n");
    assert.strictEqual(fs.existsSync(path.join(copied.path, "unlisted.tmp")), false);
    assert.strictEqual(copied.local_changes_applied, true);
    assert.deepStrictEqual(copied.copied_files.sort(), [".env", "AGENTS.override.md"].sort());
  });

  await ok("活动 worktree 即使干净也拒绝删除", async () => {
    fs.writeFileSync(path.join(copied.path, "tracked.txt"), "before\n", "utf8");
    await expectCode(WT.removeWorktree(repo, copied.path,
      { expected_id: copied.id, active_path: copied.path }, { managedRoot }), "ACTIVE_WORKTREE");
  });

  await ok("被修改或新产生的 ignored 文件不因 git status 干净而被删除", async () => {
    fs.writeFileSync(path.join(copied.path, ".env"), "TOKEN=changed-in-worktree\n", "utf8");
    let listed = await WT.listWorktrees(repo, { managedRoot });
    let item = listed.items.find((value) => value.id === copied.id);
    assert(item && item.ignored_risk_files === 1 && item.dirty);
    await expectCode(WT.removeWorktree(repo, copied.path,
      { expected_id: copied.id }, { managedRoot }), "IGNORED_WORKTREE_FILES");
    fs.writeFileSync(path.join(copied.path, ".env"), "TOKEN=test-only\n", "utf8");
    fs.writeFileSync(path.join(copied.path, "unlisted.tmp"), "new ignored output\n", "utf8");
    listed = await WT.listWorktrees(repo, { managedRoot });
    item = listed.items.find((value) => value.id === copied.id);
    assert(item && item.ignored_risk_files === 1);
    fs.rmSync(path.join(copied.path, "unlisted.tmp"));
  });

  await ok("未修改的 include 忽略文件不会阻止安全回收", async () => {
    const removed = await WT.removeWorktree(repo, copied.path,
      { expected_id: copied.id }, { managedRoot });
    assert.strictEqual(removed.ok, true);
  });

  const commitWt = await WT.createWorktree(repo, { label: "commit", copy_ignored: false }, { managedRoot, keep: 15 });
  fs.writeFileSync(path.join(commitWt.path, "tracked.txt"), "committed in detached\n", "utf8");
  git(commitWt.path, "add", "tracked.txt");
  git(commitWt.path, "commit", "--quiet", "-m", "detached commit");
  await ok("detached 独有提交未建分支时拒绝删除", async () => {
    await expectCode(WT.removeWorktree(repo, commitWt.path,
      { expected_id: commitWt.id }, { managedRoot }), "UNANCHORED_COMMITS");
  });

  await ok("仓库外或未登记目录无法伪装成托管 worktree", async () => {
    await expectCode(WT.removeWorktree(repo, repo,
      { expected_id: "anything" }, { managedRoot }), "NOT_MANAGED_WORKTREE");
  });

  console.log(`\ntest_worktree_manager: ${passed}/${passed} 全部通过`);
} finally {
  try {
    const raw = git(repo, "worktree", "list", "--porcelain");
    for (const line of raw.split(/\r?\n/)) {
      if (!line.startsWith("worktree ")) continue;
      const target = line.slice("worktree ".length);
      if (WT._within(managedRoot, target)) {
        try { git(repo, "worktree", "remove", "--force", "--", target); } catch (_e) { /* */ }
      }
    }
  } catch (_e) { /* */ }
  fs.rmSync(tmp, { recursive: true, force: true });
}
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
