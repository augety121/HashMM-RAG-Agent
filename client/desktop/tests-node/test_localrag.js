/** test_localrag.js — 本地知识库引擎单测（BM25 + 文件管理 + 增量同步）。纯逻辑。 */
"use strict";
const assert = require("assert");
const { LocalIndex, tokenize, chunkText, _under } = require("../localrag.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}
const longText = (seed) => (seed + "。").repeat(60);

console.log("=== 分词 / 分块（基础保持） ===");
ok("中文 2-gram", tokenize("知识库").includes("知识") && tokenize("知识库").includes("识库"));
ok("英文词", tokenize("hello world").includes("hello"));
ok("短文本单块", chunkText("短句").length === 1);
ok("长文本多块", chunkText("a".repeat(2000)).length > 1);

console.log("=== 摄取 + 去重（修复重复摄取翻倍）===");
let idx = new LocalIndex();
const n1 = idx.addDocument("/d/a.md", longText("检索原理"), { folder: "/d", mtime: 100, size: 2000 });
ok("首次摄取产生切片", n1 > 0 && idx.stat().chunks === n1);
const before = idx.stat().chunks;
idx.addDocument("/d/a.md", longText("检索原理"), { folder: "/d", mtime: 100, size: 2000 });
ok("重复摄取不翻倍(去重)", idx.stat().chunks === before);
ok("文件数仍为1", idx.stat().files === 1);
idx.addDocument("/d/a.md", longText("全新内容不一样的"), { folder: "/d", mtime: 200, size: 2500 });
ok("内容更新→替换", idx.stat().files === 1);
ok("注册表 mtime 更新", idx.fileList()[0].mtime === 200);
ok("注册表 size 更新", idx.fileList()[0].size === 2500);

console.log("=== 多文件 / 文件夹聚合 ===");
idx = new LocalIndex();
idx.addDocument("/proj/a.md", longText("甲文档"), { folder: "/proj", mtime: 1, size: 100 });
idx.addDocument("/proj/b.md", longText("乙文档"), { folder: "/proj", mtime: 2, size: 200 });
idx.addDocument("/other/c.md", longText("丙文档"), { folder: "/other", mtime: 3, size: 300 });
ok("3 文件", idx.stat().files === 3);
ok("2 文件夹", idx.stat().folders === 2);
ok("总字节累加", idx.stat().bytes === 600);
const folders = idx.folderList();
ok("文件夹列表含 /proj", folders.some(f => f.folder === "/proj" && f.files === 2));
ok("文件列表逐文件切片数", idx.fileList().every(f => f.chunks > 0));

console.log("=== 移除单文件 / 单文件夹 ===");
const rmA = idx.removeFile("/proj/a.md");
ok("移除文件返回切片数", rmA > 0);
ok("移除后文件数减一", idx.stat().files === 2);
ok("移除不存在文件→0", idx.removeFile("/nope.md") === 0);
const rmF = idx.removeFolder("/proj");
ok("移除文件夹返回 {files,chunks}", rmF.files === 1 && rmF.chunks > 0);
ok("移除文件夹后只剩 /other", idx.stat().files === 1 && idx.folderList()[0].folder === "(未分组)" === false);
ok("移除空文件夹→0", idx.removeFolder("/empty").files === 0);

console.log("=== 移除后 BM25 统计正确（reindex）===");
idx = new LocalIndex();
idx.addDocument("/x/keep.md", longText("保留文档关键词独有特征"), { folder: "/x", mtime: 1, size: 100 });
idx.addDocument("/x/drop.md", longText("删除文档另一个词"), { folder: "/x", mtime: 2, size: 100 });
const termsBefore = idx.stat().terms;
idx.removeFile("/x/drop.md");
ok("移除后词表收缩", idx.stat().terms < termsBefore);
ok("移除后检索保留文档仍命中", idx.search("保留文档", 3).length > 0);
ok("移除后检索已删文档无命中", idx.search("删除文档另一个词", 3).every(r => r.file !== "/x/drop.md"));
ok("chunk id 连续(reindex)", idx.chunks.every((c, i) => c.id === i));

console.log("=== 增量同步 diffFolder（纯函数）===");
idx = new LocalIndex();
idx.addDocument("/sync/a.md", longText("a"), { folder: "/sync", mtime: 100, size: 1000 });
idx.addDocument("/sync/b.md", longText("b"), { folder: "/sync", mtime: 200, size: 2000 });
const diff = idx.diffFolder("/sync", [
  { path: "/sync/a.md", mtime: 100, size: 1000 },   // 未变
  { path: "/sync/b.md", mtime: 999, size: 2000 },   // mtime 变 → 改动
  { path: "/sync/c.md", mtime: 300, size: 500 },    // 新增
]);                                                  // a 在磁盘，b 改，c 新增，无删除
ok("未变识别", diff.unchanged.includes("/sync/a.md"));
ok("改动识别(mtime)", diff.changed.includes("/sync/b.md"));
ok("新增识别", diff.added.includes("/sync/c.md"));
const diff2 = idx.diffFolder("/sync", [{ path: "/sync/a.md", mtime: 100, size: 1000 }]);
ok("删除识别(磁盘没了)", diff2.removed.includes("/sync/b.md"));
const diff3 = idx.diffFolder("/sync", [{ path: "/sync/a.md", mtime: 100, size: 9999 }]);
ok("size 变也算改动", diff3.changed.includes("/sync/a.md"));
ok("空磁盘→全删除", idx.diffFolder("/sync", []).removed.length === 2);
ok("路径分隔符兼容(反斜杠)", _under("C:\\docs\\a.md", "C:/docs"));

console.log("=== 序列化 + 向后兼容旧库 ===");
idx = new LocalIndex();
idx.addDocument("/s/a.md", longText("序列化测试"), { folder: "/s", mtime: 5, size: 50 });
const json = idx.toJSON();
ok("toJSON v2 含 files", json.v === 2 && Array.isArray(json.files));
const restored = LocalIndex.fromJSON(json);
ok("fromJSON 恢复文件注册表", restored.stat().files === 1 && restored.fileList()[0].mtime === 5);
ok("恢复后检索可用", restored.search("序列化测试", 1).length > 0);
// 旧库（v1，无 files）→ 从 chunks 派生
const oldJson = { v: 1, chunks: [{ id: 0, file: "/old/x.md", text: "旧库内容词", len: 3 }], df: [["旧库", 1], ["库内", 1], ["内容", 1]], tf: [[["旧库", 1], ["库内", 1], ["内容", 1]]], totalLen: 3 };
const oldIdx = LocalIndex.fromJSON(oldJson);
ok("旧库 v1 可加载", oldIdx.stat().chunks === 1);
ok("旧库派生文件注册表", oldIdx.fileList().length === 1 && oldIdx.fileList()[0].file === "/old/x.md");
ok("旧库 mtime 未知=0(待同步补全)", oldIdx.fileList()[0].mtime === 0);
ok("坏 JSON→空索引不崩", LocalIndex.fromJSON(null).stat().chunks === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
