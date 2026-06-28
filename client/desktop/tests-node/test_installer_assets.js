/** test_installer_assets.js — V100 微信式单窗安装界面资产守卫。
 *  V100 精髓：整页就是一张设计好的图（installerWelcome.bmp 493×312 铺满全窗页），
 *  NSIS 侧只叠一个勾选框，按钮/协议链接靠点击坐标 hit-test（几何来自
 *  hm-welcome-geometry.nsh，资产脚本与图一并导出 → 单一事实来源）。
 *  覆盖：BMP 魔数/位深/精确尺寸、几何文件 13 个 define 齐全且自洽、
 *  electron-builder.yml 引用与行为项、installer.nsh 六个约定宏 + 关键文案 +
 *  hm-eula.txt 存在、（本机有 makensis 则）harness 真编译 0 警告。 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const BUILD = path.join(ROOT, "build");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// ── 1. BMP 资产：魔数 + 24bit + 精确尺寸 ──
function bmpInfo(file) {
  const buf = fs.readFileSync(file);
  assert.ok(buf.length > 54, `${path.basename(file)} 太小，不是合法 BMP`);
  assert.strictEqual(buf.toString("ascii", 0, 2), "BM", `${path.basename(file)} 魔数不是 BM`);
  return {
    width: buf.readInt32LE(18),
    height: buf.readInt32LE(22),
    bpp: buf.readUInt16LE(28),
    compression: buf.readUInt32LE(30),
  };
}
const expect = {
  "installerSidebar.bmp": [164, 314],
  "uninstallerSidebar.bmp": [164, 314],
  "installerHeader.bmp": [150, 57],
  "installerWelcome.bmp": [493, 312],   // V100 整页单图
};
for (const [name, [w, h]] of Object.entries(expect)) {
  const info = bmpInfo(path.join(BUILD, name));
  assert.strictEqual(info.width, w, `${name} 宽应为 ${w}，实际 ${info.width}`);
  assert.strictEqual(info.height, h, `${name} 高应为 ${h}，实际 ${info.height}`);
  assert.strictEqual(info.bpp, 24, `${name} 应为 24 位（NSIS 兼容），实际 ${info.bpp}`);
  assert.strictEqual(info.compression, 0, `${name} 应为无压缩 BI_RGB`);
}
ok("品牌 BMP：BM 魔数 / 24bit 无压缩 / 尺寸精确（164×314 ×2 + 150×57 + 整页 493×312）");

// icon.ico 也要在（installerIcon/uninstallerIcon 引用）
assert.ok(fs.existsSync(path.join(BUILD, "icon.ico")), "build/icon.ico 缺失");
ok("icon.ico 在位（installerIcon/uninstallerIcon）");

// ── 2. 欢迎页几何：内联在 installer.nsh（V100 不再用二级 include），13 个 define 齐全且矩形自洽 ──
const nshForGeo = fs.readFileSync(path.join(BUILD, "installer.nsh"), "utf8");
// 严禁回退到二级 include（真机分发会丢 → 曾致打包失败）
assert.ok(!/^\s*!include\s+["'][^"'\n]*hm-welcome-geometry/m.test(nshForGeo),
  "installer.nsh 不得再 !include 独立几何文件（必须内联，否则真机打包会 could-not-find）");
assert.ok(!fs.existsSync(path.join(BUILD, "hm-welcome-geometry.nsh")),
  "独立几何文件应已删除（几何已内联进 installer.nsh）");
// ★ 版本脆弱构造 lint：muiPageLoadFullWindow/muiPageUnloadFullWindow 是较新 NSIS 才
// 抽出的独立函数，electron-builder 自带 NSIS 3.0.4.1 没有 → Call 会链接失败。
// 沙箱 makensis(v3.09) 有这函数会"假绿"，故必须靠静态检查挡住，不能只靠编译。
// 先剥掉 NSIS 行注释（; 之后），只在真实代码上匹配。
const nshCode = nshForGeo.split("\n").map((l) => l.replace(/;.*$/, "")).join("\n");
assert.ok(!/\bCall\s+(un\.)?muiPage(Load|Unload)FullWindow\b/.test(nshCode),
  "installer.nsh 不得 Call muiPageLoadFullWindow/muiPageUnloadFullWindow（新版 NSIS 才有，3.0.4.1 链接失败）；全窗请用铺满+SetWindowPos 置顶的稳定原语");
const geo = {};
for (const m of nshForGeo.matchAll(/!define\s+HM_GEO_(\w+)\s+(\d+)/g)) geo[m[1]] = Number(m[2]);
for (const k of ["W", "H", "BTN_L", "BTN_T", "BTN_R", "BTN_B", "LINK_L", "LINK_T", "LINK_R", "LINK_B", "CB_X", "CB_Y", "CB_S", "PATH_X", "PATH_Y", "PATH_W", "PATH_H"]) {
  assert.ok(k in geo, `hm-welcome-geometry.nsh 缺 HM_GEO_${k}`);
}
assert.strictEqual(geo.W, 493, "几何画布宽应与 BMP 一致 493");
assert.strictEqual(geo.H, 312, "几何画布高应与 BMP 一致 312");
assert.ok(geo.BTN_L > 0 && geo.BTN_L < geo.BTN_R && geo.BTN_R < geo.W, "按钮矩形横向应落在画布内");
assert.ok(geo.BTN_T > 0 && geo.BTN_T < geo.BTN_B && geo.BTN_B < geo.H, "按钮矩形纵向应落在画布内");
assert.ok(geo.LINK_L > 0 && geo.LINK_L < geo.LINK_R && geo.LINK_R <= geo.W, "链接矩形横向应落在画布内");
assert.ok(geo.CB_X > 0 && geo.CB_X < geo.LINK_L, "勾选框应在协议链接左侧");
assert.ok(geo.PATH_X > 0 && geo.PATH_W > 0 && geo.PATH_X + geo.PATH_W <= geo.W, "只读路径 label 落点应在画布内");
assert.ok(geo.PATH_Y > geo.LINK_B, "安装路径行应在协议行下方");
ok("installer.nsh 内联几何：17 个 define 齐全、按钮/链接/勾选/路径矩形自洽、无二级 include");

// ── 3. electron-builder.yml 引用全部在位 + V100/V97 行为项 ──
const yml = fs.readFileSync(path.join(ROOT, "electron-builder.yml"), "utf8");
for (const ref of [
  "installerSidebar: build/installerSidebar.bmp",
  "uninstallerSidebar: build/uninstallerSidebar.bmp",
  "installerHeader: build/installerHeader.bmp",
  "installerIcon: build/icon.ico",
  "uninstallerIcon: build/icon.ico",
  "include: build/installer.nsh",
]) {
  assert.ok(yml.includes(ref), `electron-builder.yml 缺少: ${ref}`);
}
// V100：目录页关闭（微信式直装），不能再是 true
assert.ok(/allowToChangeInstallationDirectory:\s*false/.test(yml),
  "V100 应关闭目录页：allowToChangeInstallationDirectory: false");
assert.ok(!/allowToChangeInstallationDirectory:\s*true/.test(yml),
  "electron-builder.yml 不应再保留 allowToChangeInstallationDirectory: true");
// V97 的行为项不能被这轮改丢
for (const kept of ["oneClick: false", "deleteAppDataOnUninstall: false", "runAfterFinish: true"]) {
  assert.ok(yml.includes(kept), `electron-builder.yml 丢了 V97 行为项: ${kept}`);
}
ok("electron-builder.yml：品牌引用 6 项 + 目录页关闭 + V97 行为项 3 项全在");

// ── 4. installer.nsh：electron-builder 约定宏 + 关键文案 ──
const nsh = fs.readFileSync(path.join(BUILD, "installer.nsh"), "utf8");
for (const macro of [
  "!macro customHeader",
  "!macro customInstallMode",
  "!macro customWelcomePage",
  "!macro customPageAfterChangeDir",
  "!macro customFinishPage",
  "!macro customUnWelcomePage",
]) {
  assert.ok(nsh.includes(macro), `installer.nsh 缺少 ${macro}`);
}
// V100 单窗机制关键点：全窗页模板 1044、铺满+置顶、拉伸贴图、hit-test 调用
for (const token of [
  "nsDialogs::Create 1044",                 // 整窗模板
  "SetWindowPos",                           // 置于 Z 序最前（图盖住页眉，零 $mui 依赖）
  "NSD_SetStretchedImage",                  // 整页一图：拉伸贴满客户区
  "hmPageClick",                            // 图上点击 hit-test
  "HM_GEO_PATH_X",                          // 微信式安装路径行（只读 $INSTDIR）
  "hm-welcome-geometry.nsh",                // 几何单一事实来源
  "installerWelcome.bmp",                   // 整页图资产名
  "MUI_INSTFILESPAGE_COLORS",               // 进度/卸载明细品牌配色
  "isForceCurrentInstall",                  // 跳过安装选项页
  "开始使用",                                // 完成页主按钮文案
  "运行 HashMM",                            // 完成页运行项
  "HM_WELCOME_CLASSIC",                     // V100 真机保险：一键回退标准向导
]) {
  assert.ok(nsh.includes(token), `installer.nsh 缺少关键点: ${token}`);
}
// 完成页 define 必须有 !ifndef 护栏（与 electron-builder 模板共存）
assert.ok(/!ifndef MUI_FINISHPAGE_TITLE/.test(nsh), "MUI_FINISHPAGE_TITLE 缺 !ifndef 护栏");
// hm-eula.txt 协议文本随包
assert.ok(fs.existsSync(path.join(BUILD, "hm-eula.txt")), "build/hm-eula.txt（用户协议文本）缺失");
assert.ok(nsh.includes("hm-eula.txt"), "installer.nsh 未引用 hm-eula.txt");
ok("installer.nsh：六个约定宏 + 全窗单图机制 + hit-test + 完成页接管 + hm-eula 随包");

// ── 5. （可选）makensis 真编译 harness ──
const which = spawnSync(process.platform === "win32" ? "where" : "which", ["makensis"], { encoding: "utf8" });
if (which.status === 0 && which.stdout.trim()) {
  const HN = path.join(ROOT, "scripts", "installer-harness.nsi");
  // 默认模式（自绘单窗）
  const r1 = spawnSync("makensis", ["-DBUILD=" + BUILD, HN], { encoding: "utf8", timeout: 120000 });
  assert.strictEqual(r1.status, 0, "makensis 默认模式编译失败:\n" + (r1.stdout || "") + (r1.stderr || ""));
  assert.ok(!/warning/i.test(r1.stdout || ""), "默认模式编译有警告:\n" + r1.stdout);
  // CLASSIC 回退模式（标准左图右文向导）—— 真机保险路径也必须编译干净
  const r2 = spawnSync("makensis", ["-DBUILD=" + BUILD, "-DHM_WELCOME_CLASSIC", HN], { encoding: "utf8", timeout: 120000 });
  assert.strictEqual(r2.status, 0, "makensis CLASSIC 模式编译失败:\n" + (r2.stdout || "") + (r2.stderr || ""));
  assert.ok(!/warning/i.test(r2.stdout || ""), "CLASSIC 模式编译有警告:\n" + r2.stdout);
  ok("makensis 真编译 harness 通过（默认 + CLASSIC 双模式各 0 警告）——两条路径全验");

  // ★ 高保真复核：若存在 NSIS 3.04 头文件（贴近 electron-builder 自带 3.0.4.1 的
  // MUI2 行为），用它再编译一次 harness。沙箱默认 makensis(v3.09) 对全窗函数过于
  // 宽松会"假绿"；3.04 头能精确复现真机的 "resolving muiPageUnloadFullWindow" 类错误。
  const HYB = process.env.HM_NSIS304_DIR || "/tmp/nsis-hybrid";
  if (fs.existsSync(path.join(HYB, "Contrib", "Modern UI 2", "Pages.nsh"))) {
    const env304 = Object.assign({}, process.env, { NSISDIR: HYB });
    const r304 = spawnSync("makensis", ["-NOCONFIG", "-DBUILD=" + BUILD, HN],
      { encoding: "utf8", timeout: 120000, env: env304 });
    assert.strictEqual(r304.status, 0,
      "3.04 头文件下编译失败（真机风险！）:\n" + (r304.stdout || "") + (r304.stderr || ""));
    assert.ok(!/resolving|aborting/i.test(r304.stdout || ""),
      "3.04 头文件下有未解析函数（真机会炸）:\n" + r304.stdout);
    ok("NSIS 3.04 头文件高保真复核通过（贴近 electron-builder，防全窗函数版本陷阱）");
  } else {
    console.log("  ⏭ 无 NSIS 3.04 头（/tmp/nsis-hybrid），跳过高保真复核（CI 可预置）");
  }
} else {
  console.log("  ⏭ 本机无 makensis，跳过 harness 编译（CI/沙箱里会跑）");
}

console.log(`\ntest_installer_assets: ${pass} 项全部通过`);
