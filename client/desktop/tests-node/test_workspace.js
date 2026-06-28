/** test_workspace.js — 用户数据/工作区路径管理单测（V100，纯逻辑）。
 *  运行：node desktop/storage/test_workspace.js
 */
"use strict";
const assert = require("assert");
const W = require("../storage/workspace");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 默认数据根 = 安装目录下的「HashMM Files」
{
  const root = W.defaultDataRoot("C:\\Users\\me\\AppData\\Local\\Programs\\HashMM");
  assert.strictEqual(root, "C:\\Users\\me\\AppData\\Local\\Programs\\HashMM\\HashMM Files");
}
ok("默认数据根落在安装目录内的「HashMM Files」");

// 2. 生效根：默认 vs 自定义
{
  const def = W.resolveDataRoot({ installDir: "D:\\Apps\\HashMM", configuredPath: "" });
  assert.strictEqual(def, "D:\\Apps\\HashMM\\HashMM Files", "未配置 → 安装目录内默认");
  const custom = W.resolveDataRoot({ installDir: "D:\\Apps\\HashMM", configuredPath: "E:\\MyData\\HashMM\\" });
  assert.strictEqual(custom, "E:\\MyData\\HashMM", "配置了 → 用自定义（去尾斜杠）");
}
ok("生效根：默认在安装目录 / 配置后用自定义路径");

// 3. 子目录（下载/文档/笔记）
{
  const root = "D:\\Apps\\HashMM\\HashMM Files";
  assert.strictEqual(W.downloadsDir(root), root + "\\Downloads");
  assert.strictEqual(W.documentsDir(root), root + "\\Documents");
  assert.strictEqual(W.notesDir(root), root + "\\Notes");
  assert.deepStrictEqual(W.allDirs(root), [root, root + "\\Downloads", root + "\\Documents", root + "\\Notes"]);
}
ok("子目录：Downloads/Documents/Notes + allDirs");

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

// 6. 数据目录校验
{
  assert.strictEqual(W.validateDataDir("D:\\MyData\\HashMM").ok, true);
  assert.strictEqual(W.validateDataDir("").ok, false, "空");
  assert.strictEqual(W.validateDataDir("HashMM").ok, false, "非绝对路径");
  assert.strictEqual(W.validateDataDir("D:\\").ok, false, "盘根");
  assert.strictEqual(W.validateDataDir("C:\\Windows\\System32").ok, false, "Windows 目录内");
  assert.strictEqual(W.validateDataDir("C:\\Program Files").ok, false, "Program Files 根");
}
ok("数据目录校验：合法/空/相对路径/盘根/系统目录");

console.log(`\ntest_workspace: ${pass} 项全部通过`);
