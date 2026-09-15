/** test_install_engine.js — 全自绘安装器引擎纯逻辑单测（V100）。
 *  覆盖：安装目录归一化、默认目录、危险目录拒绝、快捷方式 PS 命令（含单引号转义）、
 *  卸载注册表项、拷贝计划排除运行期数据、可读体积。
 *  运行：node desktop/installer/test_install_engine.js
 */
"use strict";
const assert = require("assert");
const E = require("./install-engine");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 归一化：补产品名 / 已带则不重复
assert.strictEqual(E.normalizeInstallDir("D:\\ruanjian"), "D:\\ruanjian\\HashMM");
assert.strictEqual(E.normalizeInstallDir("D:\\ruanjian\\HashMM"), "D:\\ruanjian\\HashMM");
assert.strictEqual(E.normalizeInstallDir("D:\\ruanjian\\hashmm"), "D:\\ruanjian\\hashmm"); // 大小写不重复
assert.strictEqual(E.normalizeInstallDir("D:\\x\\"), "D:\\x\\HashMM"); // 去尾斜杠
ok("安装目录归一化（补/不重复产品名、去尾斜杠）");

// 2. 默认目录：每用户、免管理员
assert.strictEqual(
  E.defaultInstallDir("C:\\Users\\me\\AppData\\Local"),
  "C:\\Users\\me\\AppData\\Local\\Programs\\HashMM");
ok("默认安装目录在 %LOCALAPPDATA%\\Programs\\HashMM");

// 3. 危险目录拒绝
for (const [dir, why] of [
  ["", "空"],
  ["HashMM", "无盘符"],
  ["C:\\", "盘符根"],
  ["c:\\windows", "系统目录"],
  ["C:\\Program Files", "系统目录"],
  ["C:\\Windows\\System32\\HashMM", "Windows 内"],
]) {
  const r = E.validateInstallDir(dir);
  assert.strictEqual(r.ok, false, `应拒绝(${why}): ${dir}`);
  assert.ok(r.reason, "应给拒绝原因");
}
// 合法目录放行
assert.strictEqual(E.validateInstallDir("D:\\ruanjian\\HashMM").ok, true);
assert.strictEqual(E.validateInstallDir("C:\\Users\\me\\AppData\\Local\\Programs\\HashMM").ok, true);
ok("危险目录拒绝（空/无盘符/盘根/系统目录/Windows 内）+ 合法目录放行");

// 4. 快捷方式 PowerShell 命令（含单引号转义）
{
  const cmd = E.shortcutPsCommand({
    lnkPath: "C:\\Users\\me\\Desktop\\HashMM.lnk",
    targetPath: "C:\\Apps\\HashMM\\HashMM.exe",
    iconPath: "C:\\Apps\\HashMM\\HashMM.exe",
    description: "HashMM",
  });
  assert.ok(cmd.includes("WScript.Shell"), "应走 WScript.Shell COM");
  assert.ok(cmd.includes("CreateShortcut('C:\\Users\\me\\Desktop\\HashMM.lnk')"), "应设置 lnk 路径");
  assert.ok(cmd.includes("TargetPath = 'C:\\Apps\\HashMM\\HashMM.exe'"), "应设置目标");
  assert.ok(cmd.includes(".Save()"), "应保存");
  // 单引号转义
  const cmd2 = E.shortcutPsCommand({ lnkPath: "C:\\a's\\x.lnk", targetPath: "C:\\b\\y.exe" });
  assert.ok(cmd2.includes("'C:\\a''s\\x.lnk'"), "路径里的单引号应转义成两个");
}
ok("快捷方式 PowerShell 命令（WScript.Shell + 单引号转义）");

// 5. 卸载注册表项（控制面板 Add/Remove）
{
  const reg = E.uninstallRegistry({
    installDir: "C:\\Apps\\HashMM", version: "1.5.0",
    uninstallExe: "C:\\Apps\\HashMM\\HashMM.exe", iconPath: "C:\\Apps\\HashMM\\HashMM.exe",
  });
  assert.strictEqual(reg.root, "HKCU", "每用户写 HKCU（免管理员）");
  assert.ok(reg.key.endsWith("Uninstall\\HashMM"), "键路径正确");
  assert.strictEqual(reg.values.DisplayName, "HashMM");
  assert.strictEqual(reg.values.DisplayVersion, "1.5.0");
  assert.ok(reg.values.UninstallString.includes("--uninstall"), "卸载串带 --uninstall");
  assert.strictEqual(reg.values.InstallLocation, "C:\\Apps\\HashMM");
}
ok("卸载注册表项（HKCU、DisplayName/Version、--uninstall）");

// 6. 拷贝计划：排除运行期数据
{
  const plan = E.copyPlan("C:\\tmp\\extract", "C:\\Apps\\HashMM");
  assert.strictEqual(plan.from, "C:\\tmp\\extract");
  assert.strictEqual(plan.to, "C:\\Apps\\HashMM");
  assert.ok(plan.exclude.includes("data"), "应排除 data（运行期数据不进安装目录）");
  assert.ok(plan.exclude.includes(E.MARKER), "应排除安装标记自身");
}
ok("拷贝计划（目标 + 排除 data/缓存/标记）");

// 7. 安装标记
{
  assert.strictEqual(E.markerPath("C:\\Apps\\HashMM"), "C:\\Apps\\HashMM\\" + E.MARKER);
  const m = E.markerContent("1.5.0", "C:\\dl\\HashMM-Setup.exe");
  assert.strictEqual(m.product, "HashMM");
  assert.strictEqual(m.version, "1.5.0");
  assert.ok(m.installedAt && m.source.includes("Setup"), "标记含时间与来源");
}
ok("安装标记（路径 + 内容含版本/时间/来源）");

// 8. 可读体积
assert.strictEqual(E.humanSize(540 * 1024 * 1024), "540 MB");
assert.strictEqual(E.humanSize(422 * 1024 * 1024 * 1024), "422.0 GB");
ok("可读体积（字节 → MB/GB）");

// 9. copyTree：真实拷贝 + 幂等（可重复安装，不再 EEXIST）+ 排除项
{
  const fs = require("fs"); const path = require("path"); const os = require("os");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hminst-"));
  const src = path.join(root, "src"), dst = path.join(root, "dst");
  fs.mkdirSync(path.join(src, "resources"), { recursive: true });
  fs.writeFileSync(path.join(src, "HashMM.exe"), "exe");
  fs.writeFileSync(path.join(src, "resources", "app.asar"), "ASAR-BYTES"); // 关键：当文件
  fs.mkdirSync(path.join(src, "data"), { recursive: true });
  fs.writeFileSync(path.join(src, "data", "user.db"), "should-be-excluded");

  E.copyTree(fs, src, dst, ["data"], path);
  assert.ok(fs.existsSync(path.join(dst, "HashMM.exe")), "应拷 exe");
  assert.strictEqual(fs.readFileSync(path.join(dst, "resources", "app.asar"), "utf8"), "ASAR-BYTES",
    "app.asar 应作为文件原样拷贝（不是被 mkdir 成目录）");
  assert.ok(!fs.existsSync(path.join(dst, "data")), "data 应被排除（运行期数据不进安装目录）");

  // 再拷一次（目标已存在）—— 必须幂等、不抛 EEXIST（修复用户实测的报错）
  assert.doesNotThrow(() => E.copyTree(fs, src, dst, ["data"], path),
    "重复安装应幂等，不得 EEXIST");
  assert.strictEqual(fs.readFileSync(path.join(dst, "resources", "app.asar"), "utf8"), "ASAR-BYTES");
  fs.rmSync(root, { recursive: true, force: true });
}
ok("copyTree 真实拷贝 + app.asar 作文件 + 排除 data + 重复安装幂等（修 EEXIST）");

// 10. 卸载目标清单（与安装对称）
{
  const t = E.uninstallTargets({
    installDir: "C:\\Apps\\HashMM",
    appData: "C:\\Users\\me\\AppData\\Roaming",
    homeDir: "C:\\Users\\me",
  });
  assert.strictEqual(t.installDir, "C:\\Apps\\HashMM");
  assert.ok(t.registryKey.endsWith("Uninstall\\HashMM"), "卸载注册表键");
  assert.strictEqual(t.shortcuts.length, 2, "开始菜单 + 桌面两个快捷方式");
  assert.ok(t.shortcuts[0].includes("Start Menu") && t.shortcuts[0].endsWith("HashMM.lnk"));
  assert.ok(t.shortcuts[1].includes("Desktop") && t.shortcuts[1].endsWith("HashMM.lnk"));
}
ok("卸载目标清单（安装目录 + 注册表键 + 开始菜单/桌面快捷方式）");

// 11. 自删脚本：等进程退出 → 删注册表/快捷方式/目录 → 删自身
{
  const bat = E.selfDeleteScript({
    installDir: "C:\\Apps\\HashMM",
    registryKey: "HKCU\\Software\\...\\Uninstall\\HashMM",
    shortcuts: ["C:\\sm\\HashMM.lnk", "C:\\dt\\HashMM.lnk"],
  });
  assert.ok(/tasklist .*HashMM\.exe/.test(bat), "应等 HashMM.exe 退出");
  assert.ok(/goto wait/.test(bat), "应轮询等待");
  assert.ok(/reg delete ".*Uninstall\\HashMM" \/f/.test(bat), "应删注册表键");
  assert.ok(bat.includes('del /f /q "C:\\sm\\HashMM.lnk"'), "应删开始菜单快捷方式");
  assert.ok(bat.includes('del /f /q "C:\\dt\\HashMM.lnk"'), "应删桌面快捷方式");
  assert.ok(bat.includes('rd /s /q "C:\\Apps\\HashMM"'), "应删整个安装目录");
  assert.ok(bat.includes('del /f /q "%~f0"'), "最后应删自身");
  assert.ok(bat.startsWith("@echo off"), "标准批处理头");
  // 带 preserveDir：保留数据夹（不整目录 rd，逐项删除但跳过数据夹）
  const bat2 = E.selfDeleteScript({
    installDir: "C:\\Apps\\HashMM", registryKey: "HKCU\\...\\HashMM",
    shortcuts: [], preserveDir: "HashMM Files",
  });
  assert.ok(!/rd \/s \/q "C:\\Apps\\HashMM"\s/.test(bat2), "保留模式不应整目录 rd");
  assert.ok(bat2.includes('not "%%~nxD"=="HashMM Files"'), "应跳过数据夹");
  assert.ok(bat2.includes('del /f /q "C:\\Apps\\HashMM\\*.*"'), "应删根文件保留子数据夹");
  // 多个保留数据夹（工作区 + 后端数据）：链式 if 两个都跳过
  const bat3 = E.selfDeleteScript({
    installDir: "C:\\Apps\\HashMM", registryKey: "HKCU\\...\\HashMM",
    shortcuts: [], preserveDirs: ["HashMM Files", "HashMM Data", "local-backend"],
  });
  assert.ok(bat3.includes('if /i not "%%~nxD"=="HashMM Files" if /i not "%%~nxD"=="HashMM Data" if /i not "%%~nxD"=="local-backend" rd /s /q'),
    "两个保留夹链式 if 都跳过");
  assert.ok(!/rd \/s \/q "C:\\Apps\\HashMM"\s/.test(bat3), "多保留模式不整目录 rd");
}
ok("自删脚本（等退出 → 删注册表/快捷方式/目录 → 删自身；preserveDir 保留数据夹）");

// installedExeName：取自 process.execPath（真 app 二进制），不从 portable 启动器名推导
{
  assert.strictEqual(E.installedExeName("C:\\Temp\\abc\\HashMM.exe"), "HashMM.exe", "取真二进制名");
  assert.strictEqual(E.installedExeName("/tmp/x/HashMM.exe"), "HashMM.exe", "正斜杠也行");
  assert.strictEqual(E.installedExeName("C:\\dl\\HashMM-安装-1.6.0.exe"), "HashMM-安装-1.6.0.exe", "若真是该名则原样（实际 execPath 不会是这个）");
  assert.strictEqual(E.installedExeName("C:\\x\\noext"), "HashMM.exe", "无 .exe 兜底");
  assert.strictEqual(E.installedExeName(""), "HashMM.exe", "空兜底");
}
ok("installedExeName：从真 app 二进制取名（修 exe 名推导 bug）");

// 上次安装记录：路径在 userData，内容含 installDir/version/exeName
{
  const p = E.lastInstallRecordPath("C:\\Users\\me\\AppData\\Roaming\\HashMM");
  assert.ok(p.endsWith(".hashmm-last-install.json"), "记录文件名");
  assert.ok(p.includes("Roaming\\HashMM"), "落在 userData");
  const rec = E.lastInstallRecord("D:\\hashmm", "1.6.0", "HashMM.exe");
  assert.strictEqual(rec.installDir, "D:\\hashmm");
  assert.strictEqual(rec.version, "1.6.0");
  assert.strictEqual(rec.exeName, "HashMM.exe");
  assert.ok(rec.at, "含时间戳");
  assert.strictEqual(E.lastInstallRecord("D:\\x").exeName, "HashMM.exe", "默认 exe 名");
}
ok("上次安装记录：userData 路径 + 内容（供再次运行检测已安装）");

// 卸载快捷方式：shortcutPsCommand 支持 arguments（--uninstall）
{
  const cmd = E.shortcutPsCommand({
    lnkPath: "C:\\sm\\卸载 HashMM.lnk", targetPath: "D:\\hashmm\\HashMM.exe",
    workingDir: "D:\\hashmm", iconPath: "D:\\hashmm\\HashMM.exe",
    description: "卸载 HashMM", arguments: "--uninstall",
  });
  assert.ok(cmd.includes("$s.Arguments = '--uninstall'"), "应写入 --uninstall 参数");
  assert.ok(cmd.includes("$s.TargetPath = 'D:\\hashmm\\HashMM.exe'"), "目标=已装 exe");
  // 不给 arguments 时不写 Arguments 行（向后兼容）
  const cmd2 = E.shortcutPsCommand({ lnkPath: "a", targetPath: "b" });
  assert.ok(!cmd2.includes("$s.Arguments"), "无参数不写 Arguments");
}
ok("卸载快捷方式：shortcutPsCommand 支持 --uninstall 参数");

console.log(`\ntest_install_engine: ${pass} 项全部通过`);
