/** test_storage.js — 主进程存储服务集成测试（V100，真实 fs，临时目录）。
 *  运行：node desktop/storage/test_storage.js
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { Storage } = require("./index");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

function mkInstall() { return fs.mkdtempSync(path.join(os.tmpdir(), "hminst-")); }

// 1. 默认数据根在安装目录内 + ensureDirs 建出结构
{
  const inst = mkInstall();
  const s = new Storage({ installDir: inst });
  assert.ok(s.isDefault(), "初始为默认");
  const root = s.ensureDirs();
  assert.strictEqual(root, path.join(inst, "HashMM Files"), "默认根在安装目录内（V312：平台 path，不再硬编码 win32）");
  // V312 强化：目录必须【真的】建在安装目录里（旧版 win32 拼接在 Linux 上写进 CWD 的
  // 字面量反斜杠目录——本仓库根曾因此堆了 90 个 `\tmp\hminst-*` 垃圾）。
  assert.ok(fs.existsSync(path.join(root, "Downloads")), "Downloads 真实建在数据根内");
  assert.ok(!fs.readdirSync(process.cwd()).some((n) => n.includes("\\")),
            "CWD 不得出现含反斜杠的字面量垃圾（V312 回归锁）");
  fs.rmSync(inst, { recursive: true, force: true });
}
ok("默认数据根在安装目录内 + ensureDirs 不抛");

// 2. saveFile 真实写入 + 防覆盖去重
{
  const inst = mkInstall();
  const s = new Storage({ installDir: inst });
  s._configured = path.join(inst, "data");   // 自定义根（平台路径，V312 起默认根同样可用）
  const r1 = s.saveFile("hello.md", "# hi", "notes");
  assert.ok(r1.ok && fs.existsSync(r1.path), "首个文件写入成功");
  assert.ok(r1.path.endsWith("hello.md"));
  const r2 = s.saveFile("hello.md", "# hi again", "notes");
  assert.ok(r2.ok && r2.path !== r1.path, "同名第二个去重为不同路径");
  assert.ok(/hello \(1\)\.md$/.test(r2.path), "去重命名为 hello (1).md");
  assert.strictEqual(fs.readFileSync(r1.path, "utf8"), "# hi");
  assert.strictEqual(fs.readFileSync(r2.path, "utf8"), "# hi again");
  fs.rmSync(inst, { recursive: true, force: true });
}
ok("saveFile 真实写入 + 同名去重为 (1)");

// 3. getDownloadTarget 在 Downloads 下且不冲突
{
  const inst = mkInstall();
  const s = new Storage({ installDir: inst });
  s._configured = path.join(inst, "data");
  const t1 = s.getDownloadTarget("a.zip");
  assert.ok(t1.includes("Downloads") && t1.endsWith("a.zip"));
  fs.writeFileSync(t1, "x"); // 占位
  const t2 = s.getDownloadTarget("a.zip");
  assert.ok(/a \(1\)\.zip$/.test(t2), "已存在 → a (1).zip");
  fs.rmSync(inst, { recursive: true, force: true });
}
ok("getDownloadTarget：Downloads 下不冲突去重");

// 4. setDataRoot 校验 + 配置持久化 + 恢复默认
{
  const inst = mkInstall();
  const s = new Storage({ installDir: inst });
  const bad = s.setDataRoot("relative/path");
  assert.strictEqual(bad.ok, false, "非法路径被拒");
  // ★ V312：POSIX 上 Windows 盘符路径必须被拒（旧版放行导致 mkdir 出字面量
  //   `C:\HashMMData` 垃圾目录并进了发行包——这是回归锁）。
  if (!/^win/i.test(process.platform)) {
    const winform = s.setDataRoot("C:\\HashMMData");
    assert.strictEqual(winform.ok, false, "POSIX 上盘符路径必须被拒");
  }
  const target = path.join(inst, "custom-root");   // 平台合法绝对路径（在 tmp 沙箱内）
  const good = s.setDataRoot(target);
  assert.strictEqual(good.ok, true, "合法自定义根应被接受：" + JSON.stringify(good));
  // 重新读：另起实例应读到刚写的配置
  const s2 = new Storage({ installDir: inst });
  assert.ok(!s2.isDefault(), "自定义根被持久化，新实例读到");
  // 恢复默认
  const reset = s2.setDataRoot("");
  assert.ok(reset.ok && reset.isDefault, "空字符串恢复默认");
  const s3 = new Storage({ installDir: inst });
  assert.ok(s3.isDefault(), "恢复默认被持久化");
  fs.rmSync(inst, { recursive: true, force: true });
}
ok("setDataRoot：非法拒绝 + 自定义持久化 + 恢复默认");

console.log(`\ntest_storage: ${pass} 项全部通过`);
