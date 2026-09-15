"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const RI = require("../services/repo-intelligence");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-repo-intel-"));
let passed = 0;
function ok(name, fn) {
  fn(); passed++; console.log("  ✔ " + name);
}

async function main() {
try {
  const repo = path.join(tmp, "repo");
  const nested = path.join(repo, "services", "payments");
  const home = path.join(tmp, "home");
  fs.mkdirSync(nested, { recursive: true });
  fs.mkdirSync(path.join(home, ".hashmm"), { recursive: true });
  fs.writeFileSync(path.join(home, ".hashmm", "AGENTS.md"), "global-rule", "utf8");
  fs.writeFileSync(path.join(repo, "AGENTS.md"), "root-rule", "utf8");
  fs.writeFileSync(path.join(repo, "services", "AGENTS.md"), "service-base", "utf8");
  fs.writeFileSync(path.join(repo, "services", "AGENTS.override.md"), "service-override", "utf8");
  fs.writeFileSync(path.join(nested, "TEAM_GUIDE.md"), "payment-rule", "utf8");
  fs.writeFileSync(path.join(repo, "PLANS.md"), "root plan", "utf8");
  fs.writeFileSync(path.join(nested, "PLANS.md"), "nearest plan", "utf8");

  ok("AGENTS 按 global→root→nested 合并，override 优先且每目录只取一个", () => {
    const r = RI.discoverInstructions({ repoRoot: repo, cwd: nested, homeDir: home });
    assert.deepStrictEqual(r.sources.map((x) => path.basename(x.path)),
      ["AGENTS.md", "AGENTS.md", "AGENTS.override.md", "TEAM_GUIDE.md"]);
    assert(r.combined.indexOf("global-rule") < r.combined.indexOf("root-rule"));
    assert(r.combined.includes("service-override"));
    assert(!r.combined.includes("service-base"));
  });

  ok("指令总量按 UTF-8 字节硬截断并明确标记", () => {
    const guide = path.join(nested, "TEAM_GUIDE.md");
    fs.writeFileSync(guide, "x".repeat(2000), "utf8");
    const r = RI.discoverInstructions({ repoRoot: repo, cwd: nested, homeDir: home, maxBytes: 20 });
    fs.writeFileSync(guide, "payment-rule", "utf8");
    assert.strictEqual(r.bytes, 1024); // production lower bound is intentional
    assert(r.max_bytes === 1024);
    assert.strictEqual(r.truncated, true);
  });

  ok("PLANS.md 使用离当前目录最近的模板", () => {
    const p = RI.discoverPlanTemplate(repo, nested);
    assert(p && p.relative.endsWith(path.join("services", "payments", "PLANS.md")));
    assert.strictEqual(p.content, "nearest plan");
  });

  ok("porcelain -z 正确解析普通、未跟踪与 rename", () => {
    const rows = RI.parsePorcelainZ(" M src/a.js\0?? new file.txt\0R  dst.js\0src.js\0");
    assert.deepStrictEqual(rows.map((x) => x.path), ["src/a.js", "new file.txt", "dst.js"]);
    assert.strictEqual(rows[1].untracked, true);
    assert.strictEqual(rows[2].original_path, "src.js");
  });

  ok("统一 diff 按文件切块，不把文件名靠模型猜", () => {
    const diff = "diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1 +1 @@\n-a\n+b\n" +
      "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -2 +2 @@\n-x\n+y\n";
    const chunks = RI.splitUnifiedDiff(diff);
    assert.deepStrictEqual(chunks.map((x) => x.file), ["a.js", "b.py"]);
    assert(chunks[0].diff.includes("+b"));
  });

  ok("路径边界拒绝仓库外 real path", () => {
    assert.strictEqual(RI._within(repo, nested), true);
    assert.strictEqual(RI._within(repo, home), false);
  });

  const nonGit = await RI.inspectRepository(nested, { homeDir: home });
  ok("非 Git 工作区仍加载当前目录指令与最近 PLANS.md", () => {
    assert.strictEqual(nonGit.ok, true);
    assert.strictEqual(nonGit.is_git, false);
    assert(nonGit.instructions.combined.includes("payment-rule"));
    assert(nonGit.plan && nonGit.plan.content === "nearest plan");
  });

  const gitRepo = path.join(tmp, "git-repo");
  fs.mkdirSync(gitRepo, { recursive: true });
  execFileSync("git", ["init", "--quiet", gitRepo], { windowsHide: true });
  execFileSync("git", ["-C", gitRepo, "config", "user.email", "hashmm-test@example.invalid"], { windowsHide: true });
  execFileSync("git", ["-C", gitRepo, "config", "user.name", "HashMM Test"], { windowsHide: true });
  fs.writeFileSync(path.join(gitRepo, "tracked.txt"), "before\n", "utf8");
  fs.writeFileSync(path.join(gitRepo, "AGENTS.md"), "review only changed lines", "utf8");
  execFileSync("git", ["-C", gitRepo, "add", "tracked.txt", "AGENTS.md"], { windowsHide: true });
  execFileSync("git", ["-C", gitRepo, "commit", "--quiet", "-m", "fixture"], { windowsHide: true });
  fs.writeFileSync(path.join(gitRepo, "tracked.txt"), "after\n", "utf8");
  const inspected = await RI.inspectRepository(gitRepo, { homeDir: home });
  ok("真实 Git 仓库检查返回分支、HEAD、状态、diff 与指令链", () => {
    assert.strictEqual(inspected.ok, true);
    assert.strictEqual(inspected.is_git, true);
    assert(inspected.branch);
    assert(/^[0-9a-f]{40}$/i.test(inspected.head));
    assert(inspected.files.some((x) => x.path === "tracked.txt" && x.worktree === "M"));
    assert(inspected.unstaged.text.includes("+after"));
    assert(inspected.unstaged.chunks.some((x) => x.file === "tracked.txt"));
    assert(inspected.instructions.combined.includes("review only changed lines"));
  });

  console.log(`\ntest_repo_intelligence: ${passed}/${passed} 全部通过`);
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
