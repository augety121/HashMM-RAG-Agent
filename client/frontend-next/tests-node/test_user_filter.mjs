/** test_user_filter.mjs — 用户筛选/排序/角色统计纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "uf-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let U;
try {
  const base = ["lib/userFilter.ts", "lib/types.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  U = await import(url.pathToFileURL(path.join(tmp, "userFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const US = [
  { id: "1", username: "alice", display_name: "Alice", role: "admin", created_at: 300 },
  { id: "2", username: "bob", display_name: "Bob", role: "user", created_at: 100 },
  { id: "3", username: "carol", display_name: "Carol", role: "viewer", created_at: 200 },
  { id: "4", username: "dave", display_name: "Dave", role: "user", created_at: 400 },
];

console.log("=== filterUsers ===");
ok("默认全返回", U.filterUsers(US).length === 4);
ok("按角色 admin", U.filterUsers(US, { role: "admin" }).length === 1);
ok("按角色 user", U.filterUsers(US, { role: "user" }).length === 2);
ok("关键词-用户名", U.filterUsers(US, { query: "bob" }).length === 1);
ok("关键词-显示名", U.filterUsers(US, { query: "Carol" }).length === 1);
ok("关键词大小写不敏感", U.filterUsers(US, { query: "ALICE" }).length === 1);
ok("组合: user + d", U.filterUsers(US, { role: "user", query: "dave" }).length === 1);
ok("空输入安全", U.filterUsers(null).length === 0);

console.log("=== sortUsers ===");
ok("按名字", U.sortUsers(US, "name").map(u => u.username).join(",") === "alice,bob,carol,dave");
ok("按角色(admin先)", U.sortUsers(US, "role")[0].role === "admin");
ok("按角色(viewer末)", U.sortUsers(US, "role")[3].role === "viewer");
ok("按时间(新→旧)", U.sortUsers(US, "created")[0].username === "dave");
ok("不改原数组", (() => { const before = US.map(u => u.id).join(); U.sortUsers(US, "name"); return US.map(u => u.id).join() === before; })());

console.log("=== userRoleCounts ===");
const c = U.userRoleCounts(US);
ok("总数", c.total === 4);
ok("admin 数", c.admin === 1);
ok("user 数", c.user === 2);
ok("viewer 数", c.viewer === 1);
ok("空→全0", U.userRoleCounts([]).total === 0 && U.userRoleCounts([]).admin === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
