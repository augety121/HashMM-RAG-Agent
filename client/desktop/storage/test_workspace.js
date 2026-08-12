/** test_workspace.js — 用户数据/工作区路径管理单测（V100，纯逻辑）。
 *  运行：node desktop/storage/test_workspace.js
 */
"use strict";
const assert = require("assert");
const path = require("path");
const W = require("./workspace");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1-3. 路径拼接（V312：平台化——win32 形态用显式注入验证，POSIX 用平台默认验证。
//       旧版硬编码 path.win32，Linux 上把数据写进 CWD 的字面量反斜杠目录）
{
  const w32 = path.win32;
  const root = W.defaultDataRoot("C:\\Users\\me\\AppData\\Local\\Programs\\HashMM", w32);
  assert.strictEqual(root, "C:\\Users\\me\\AppData\\Local\\Programs\\HashMM\\HashMM Files");
  const def = W.resolveDataRoot({ installDir: "D:\\Apps\\HashMM", configuredPath: "", pathImpl: w32 });
  assert.strictEqual(def, "D:\\Apps\\HashMM\\HashMM Files", "未配置 → 安装目录内默认");
  const custom = W.resolveDataRoot({ installDir: "D:\\Apps\\HashMM", configuredPath: "E:\\MyData\\HashMM\\" });
  assert.strictEqual(custom, "E:\\MyData\\HashMM", "配置了 → 用自定义（去尾斜杠）");
  const r = "D:\\Apps\\HashMM\\HashMM Files";
  assert.strictEqual(W.downloadsDir(r, w32), r + "\\Downloads");
  assert.strictEqual(W.notesDir(r, w32), r + "\\Notes");
  assert.deepStrictEqual(W.allDirs(r, w32), [r, r + "\\Downloads", r + "\\Documents", r + "\\Notes"]);
}
ok("Windows 形态（显式注入 win32）：默认根/生效根/子目录/allDirs");
{
  // POSIX 平台默认（本 CI 即 Linux——修复前这些期望不可能成立）
  const root = W.defaultDataRoot("/opt/hashmm");
  assert.strictEqual(root, "/opt/hashmm/HashMM Files");
  assert.strictEqual(W.downloadsDir(root), root + "/Downloads");
  assert.strictEqual(W.resolveDataRoot({ installDir: "/opt/hashmm", configuredPath: "/data/hm/" }), "/data/hm");
  assert.strictEqual(W.configPath("/opt/hashmm"), "/opt/hashmm/.hashmm-storage.json");
}
ok("POSIX 平台默认：默认根/子目录/生效根/配置文件路径");

// 4. 文件名净化：非法字符 / 控制字符 / 首尾点空格 / 保留名 / 空 / 超长
{
  assert.strictEqual(W.sanitizeFilename('a/b:c*d?e"f<g>h|i.txt'), "a_b_c_d_e_f_g_h_i.txt");
  assert.strictEqual(W.sanitizeFilename("  ..report..  "), "report", "去首尾点与空格");
  assert.strictEqual(W.sanitizeFilename(""), "untitled", "空 → untitled");
  assert.strictEqual(W.sanitizeFilename("CON"), "_CON", "Windows 保留名加前缀");
  assert.strictEqual(W.sanitizeFilename("com1.log"), "_com1.log", "保留名(带扩展)也处理");
  const long = "x".repeat(300) + ".md";
  assert.ok(W.sanitizeFilename(long).length <= 200, "超长截断到 200");
  assert.ok(W.sanitizeFilename(long).endsWith(".md"), "截断保留扩展名");
}
ok("文件名净化：非法字符/控制字符/首尾点空格/保留名/空/超长");

// 5. 防覆盖去重：碰撞 → name (1).ext → name (2).ext
{
  const existing = new Set(["D:\\d\\note.md", "D:\\d\\note (1).md"]);
  const existsFn = (p) => existing.has(p);
  const out = W.uniqueFilePath("D:\\d", "note.md", existsFn, require("path").win32);
  assert.strictEqual(out, "D:\\d\\note (2).md", "前两个已占用 → (2)");
  const fresh = W.uniqueFilePath("D:\\d", "new.md", existsFn, require("path").win32);
  assert.strictEqual(fresh, "D:\\d\\new.md", "不冲突则原名");
  // 净化 + 去重叠加
  const dirty = W.uniqueFilePath("D:\\d", 'note:x.md', () => false, require("path").win32);
  assert.strictEqual(dirty, "D:\\d\\note_x.md", "先净化再去重");
}
ok("防覆盖去重：碰撞追加 (n) + 不冲突保留原名 + 先净化");

// 6. 数据目录校验（V312：平台感知——第二参显式指定平台，两套规则都锁）
{
  // Windows 规则组
  assert.strictEqual(W.validateDataDir("D:\\MyData\\HashMM", "win32").ok, true);
  assert.strictEqual(W.validateDataDir("", "win32").ok, false, "空");
  assert.strictEqual(W.validateDataDir("HashMM", "win32").ok, false, "非绝对路径");
  assert.strictEqual(W.validateDataDir("D:\\", "win32").ok, false, "盘根");
  assert.strictEqual(W.validateDataDir("C:\\Windows\\System32", "win32").ok, false, "Windows 目录内");
  assert.strictEqual(W.validateDataDir("C:\\Program Files", "win32").ok, false, "Program Files 根");
  // POSIX 规则组（修复前：合法绝对路径全被拒、盘符形态反被放行 → mkdir 出字面量垃圾目录）
  assert.strictEqual(W.validateDataDir("/data/hashmm", "linux").ok, true, "POSIX 合法绝对路径");
  assert.strictEqual(W.validateDataDir("C:\\HashMMData", "linux").ok, false, "POSIX 上盘符形态必须拒（垃圾目录元凶）");
  assert.strictEqual(W.validateDataDir("relative/x", "linux").ok, false, "POSIX 相对路径拒");
  assert.strictEqual(W.validateDataDir("/", "linux").ok, false, "根目录拒");
  assert.strictEqual(W.validateDataDir("/etc/hashmm", "linux").ok, false, "系统目录拒");
  assert.strictEqual(W.validateDataDir("/usr", "darwin").ok, false, "macOS 同 POSIX 规则");
}
ok("数据目录校验：Windows 规则组 + POSIX 规则组（平台感知）");

console.log(`\ntest_workspace: ${pass} 项全部通过`);
