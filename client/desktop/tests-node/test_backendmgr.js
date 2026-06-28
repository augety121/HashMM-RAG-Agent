/** desktop/tests-node/test_backendmgr.js — 本地后端管理器冒烟 v2（假 Python，沙箱可跑）。
 *
 *  V88 新增的回归用例全部来自 Windows 真机事故：
 *   - pip 按 GBK 读 requirements 炸 UnicodeDecodeError → sanitize 净化用例
 *   - venv 在、包不在 → "环境已就绪"撒谎 → envReady 真实化用例
 *   - 残局下点启动 → No module named uvicorn 裸奔 → start 前置引导用例
 *   - 端口被占 → 自动避让用例；重置环境自愈用例；UTF-8 子进程环境用例
 *
 *  假 python 行为：--version 报 3.12；-m venv 创建目录并把自己复制成 venv python；
 *  -m pip install 成功时写 venv/installed.flag（FAKE_PIP_FAIL=1 时失败退出）；
 *  -c "import ..." 按 flag 存在与否决定成败；-m uvicorn 起真 HTTP /api/health。
 *  运行：node desktop/tests-node/test_backendmgr.js
 */
"use strict";
const fs = require("fs");
const os = require("os");
const net = require("net");
const path = require("path");
const assert = require("assert");
const { BackendManager, splitCmd } = require("../backendmgr");

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "hmm-bm-"));
const FAKE = path.join(TMP, "fakepython");
const OLDPY = path.join(TMP, "oldpython");

const fakeSrc = `#!/usr/bin/env node
const http = require("http"); const fs = require("fs"); const path = require("path");
const a = process.argv.slice(2);
const flag = path.join(path.dirname(__filename), "..", "installed.flag");
if (a[0] === "--version") { console.log("Python 3.12.1"); process.exit(0); }
if (a[0] === "-m" && a[1] === "venv") {
  const dir = a[2]; fs.mkdirSync(path.join(dir, "bin"), { recursive: true });
  fs.copyFileSync(__filename, path.join(dir, "bin", "python"));
  fs.chmodSync(path.join(dir, "bin", "python"), 0o755);
  process.exit(0);
}
if (a[0] === "-m" && a[1] === "pip") {
  if (process.env.FAKE_PIP_FAIL === "1") {
    console.error("ERROR: UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1 (simulated)");
    process.exit(1);
  }
  console.log("Collecting fastapi"); console.log("Installing collected packages: fastapi uvicorn numpy");
  console.log("Successfully installed fastapi-0.110 uvicorn-0.27 numpy-1.26");
  fs.writeFileSync(flag, "1");
  process.exit(0);
}
if (a[0] === "-c" && !a[1].includes("uvicorn.run")) {
  if (fs.existsSync(flag)) process.exit(0);
  console.error("ModuleNotFoundError: No module named 'fastapi'");
  process.exit(1);
}
if (a[0] === "-c" && a[1].includes("uvicorn.run")) {
  if (!fs.existsSync(flag)) { console.error("No module named uvicorn"); process.exit(1); }
  if (process.env.PYTHONUTF8 !== "1") { console.error("missing PYTHONUTF8"); process.exit(2); }
  const src = process.env.HASHMM_SRC || "";
  if (!src || !fs.existsSync(path.join(src, "hashmm"))) {
    console.error("ModuleNotFoundError: No module named 'hashmm'"); process.exit(1);
  }
  const port = parseInt(process.env.HASHMM_PORT, 10);
  console.log("INFO: Uvicorn(fake) starting on 127.0.0.1:" + port + " cwd=" + process.cwd());
  setTimeout(() => {
    http.createServer((req, res) => {
      if (req.url === "/api/health") { res.writeHead(200, {"Content-Type":"application/json"}); return res.end('{"ok":true}'); }
      res.writeHead(404); res.end();
    }).listen(port, "127.0.0.1", () => console.log("INFO: ready"));
  }, 600);
  setInterval(() => {}, 1 << 30);
}
`;
fs.writeFileSync(FAKE, fakeSrc); fs.chmodSync(FAKE, 0o755);
fs.writeFileSync(OLDPY, `#!/usr/bin/env node\nif (process.argv[2]==="--version"){console.log("Python 3.8.10");process.exit(0);}\n`);
fs.chmodSync(OLDPY, 0o755);

const cp = require("child_process");
const wrap = (cmd, args, opts) => cp.spawnSync(process.execPath, [cmd, ...args], opts);
const wrapAsync = (cmd, args, opts) => cp.spawn(process.execPath, [cmd, ...args], opts);

function occupy(port) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(port, "127.0.0.1", () => resolve(s));
  });
}

async function main() {
  const HOME = path.join(TMP, "home");
  const SRC = path.join(TMP, "src");
  fs.mkdirSync(path.join(SRC, "hashmm"), { recursive: true });
  // 故意带中文注释/行内注释/空行/纯注释行——净化层应只留 3 个包行
  fs.writeFileSync(path.join(SRC, "requirements.txt"),
    "# HashMM 核心依赖（中文注释，模拟 GBK 炸点）\n\nfastapi>=0.110\nuvicorn[standard]>=0.27   # 服务器（行内中文）\nnumpy>=1.24\n", "utf-8");

  let pass = 0; const ok = (n) => { pass++; console.log("  ✔ " + n); };
  const lines = [];
  const mgr = new BackendManager({
    spawn: wrapAsync, spawnSync: wrap, exec: () => {}, platform: "linux",
    log: (s) => lines.push(s),
  });

  // 1. 版本检测
  mgr.detectPython(OLDPY);
  assert(lines.some((l) => l.includes("过低")), "3.8 应被标记过低");
  const det = mgr.detectPython(FAKE);
  assert(det.ok && det.version === "3.12", JSON.stringify(det));
  ok("Python 检测（过低拒绝 / 3.12 通过）");

  // 2. requirements 运行时净化（V88 真机回归：GBK/中文注释）
  const san = mgr.sanitizeRequirements(path.join(SRC, "requirements.txt"), HOME);
  assert(!san.error && san.count === 3, JSON.stringify(san));
  const sanBody = fs.readFileSync(san.file, "utf-8");
  assert(!/[^\x00-\x7F]/.test(sanBody), "净化产物必须纯 ASCII");
  assert(/^fastapi>=0\.110\nuvicorn\[standard\]>=0\.27\nnumpy>=1\.24\n$/.test(sanBody), "应只剩包行:\n" + sanBody);
  ok("requirements 净化为纯 ASCII（中文/行内注释剥离）");

  // 3. 未初始化直接 start → 引导
  let r = await mgr.start({ home: HOME, srcDir: SRC });
  assert(!r.ok && /初始化/.test(r.error));
  ok("未初始化时 start 给出引导");

  // 4. pip 失败（模拟 GBK 炸）→ setup 报错，且 envReady 不撒谎（V88 核心回归）
  process.env.FAKE_PIP_FAIL = "1";
  r = await mgr.setup({ pythonCmd: det.cmd, home: HOME, srcDir: SRC });
  delete process.env.FAKE_PIP_FAIL;
  assert(!r.ok && /安装失败/.test(r.error), JSON.stringify(r));
  assert(fs.existsSync(mgr.venvPython(HOME)), "venv 应已创建（残局形态）");
  assert(mgr.envReady(HOME) === false, "venv 在但包不在 → 必须判未就绪（不能再撒谎）");
  ok("pip 失败残局：envReady 如实报未就绪");

  // 5. 残局下点启动 → 人话引导而非 No module 裸奔
  r = await mgr.start({ home: HOME, srcDir: SRC });
  assert(!r.ok && /不完整|初始化/.test(r.error), JSON.stringify(r));
  ok("残局 start 被前置校验拦下（人话引导）");

  // 6. 正常 setup：净化安装 + 导入校验 + 就绪标记
  r = await mgr.setup({ pythonCmd: det.cmd, home: HOME, srcDir: SRC });
  assert(r.ok, JSON.stringify(r));
  assert(fs.existsSync(path.join(HOME, ".deps-ok")), "成功后应有就绪标记");
  assert(mgr.envReady(HOME, { fast: true }) === true, "fast 路径应就绪");
  assert(mgr.logs().some((l) => l.includes("Successfully installed")));
  assert(mgr.logs().some((l) => l.includes("校验核心包")));
  ok("setup 安装 + import 实测 + 标记落盘");

  // 7. 老环境兼容：标记丢失 → 完整判定实测 import 并补写标记
  fs.rmSync(path.join(HOME, ".deps-ok"));
  assert(mgr.envReady(HOME, { fast: true }) === false, "无标记 fast 应为未就绪");
  assert(mgr.envReady(HOME) === true, "完整判定应实测 import 通过");
  assert(fs.existsSync(path.join(HOME, ".deps-ok")), "完整判定通过后应补写标记");
  ok("标记缺失时实测 import 并自动补写");

  // 8. start + 健康等待 + status（同时验证 PYTHONUTF8 注入：假 uvicorn 缺它会退码 2）
  r = await mgr.start({ home: HOME, srcDir: SRC, port: 17999, healthTimeoutMs: 15000 });
  assert(r.ok && r.url === "http://127.0.0.1:17999", JSON.stringify(r));
  let st = mgr.status(HOME);
  assert(st.running && st.port === 17999 && st.envReady);
  assert(mgr.logs().some((l) => l.includes("Uvicorn(fake) starting")));
  ok("start + /api/health 就绪 + UTF-8 子进程环境");

  // 9. 重复 start 幂等
  r = await mgr.start({ home: HOME, srcDir: SRC, port: 17999 });
  assert(r.ok && r.already);
  ok("重复 start 幂等");

  // 10. stop + 退出感知
  mgr.stop();
  await new Promise((res) => setTimeout(res, 400));
  st = mgr.status(HOME);
  assert(!st.running);
  assert(mgr.logs().some((l) => l.includes("进程退出")));
  ok("stop 后进程退出被感知");

  // 11. 端口占用自动避让（V88）
  const blocker = await occupy(18100);
  r = await mgr.start({ home: HOME, srcDir: SRC, port: 18100, healthTimeoutMs: 15000 });
  assert(r.ok && r.url === "http://127.0.0.1:18101", "应自动让到 18101，实际 " + JSON.stringify(r));
  assert(mgr.logs().some((l) => l.includes("被占用，改用 18101")));
  mgr.stop(); blocker.close();
  await new Promise((res) => setTimeout(res, 300));
  ok("端口被占自动避让 +1");

  // 12. 重置环境自愈
  r = mgr.resetEnv(HOME);
  assert(r.ok && !fs.existsSync(mgr.venvPython(HOME)) && !fs.existsSync(path.join(HOME, ".deps-ok")));
  assert(mgr.envReady(HOME) === false);
  ok("resetEnv 删 venv 与标记");

  // 13. splitCmd 引号路径
  assert.deepStrictEqual(splitCmd('"C:\\Program Files\\Python\\python.exe" -X utf8'),
    ["C:\\Program Files\\Python\\python.exe", "-X", "utf8"]);
  ok("splitCmd 带空格路径解析");

  // 14. V89 内置运行时：零 venv、零标记，直接起；setup 短路；mode 透出
  const RT = path.join(TMP, "rt");
  fs.mkdirSync(path.join(RT, "python", "bin"), { recursive: true });
  const BPY = path.join(RT, "python", "bin", "python3");
  fs.writeFileSync(BPY, `#!/usr/bin/env node
const http = require("http");
const a = process.argv.slice(2);
// 模拟 Windows embeddable：存在 ._pth 即无视 PYTHONPATH —— 只有 -c 引导代码
// 显式注入 HASHMM_SRC 才找得到 hashmm（V90 真机事故的契约化复刻）
if (a[0] === "-c" && a[1].includes("uvicorn.run")) {
  const fsx = require("fs"); const pathx = require("path");
  if (process.env.PYTHONUTF8 !== "1") { console.error("missing PYTHONUTF8"); process.exit(2); }
  if (!process.env.HASHMM_JWT_SECRET || process.env.HASHMM_JWT_SECRET.length < 32) { console.error("missing JWT secret"); process.exit(3); }
  const src = process.env.HASHMM_SRC || "";
  if (!src || !fsx.existsSync(pathx.join(src, "hashmm"))) {
    console.error("ModuleNotFoundError: No module named 'hashmm'"); process.exit(1);
  }
  const port = parseInt(process.env.HASHMM_PORT, 10);
  http.createServer((req, res) => {
    if (req.url === "/api/health") { res.writeHead(200); return res.end("{}"); }
    res.writeHead(404); res.end();
  }).listen(port, "127.0.0.1", () => console.log("bundled ready"));
  setInterval(() => {}, 1 << 30);
}
`);
  fs.chmodSync(BPY, 0o755);
  const HOME2 = path.join(TMP, "home2");          // 干净 home：无 venv 无标记
  assert.strictEqual(mgr.bundledPython(RT), BPY);
  assert(mgr.envReady(HOME2, { rtDir: RT }) === true, "内置运行时应恒就绪");
  r = await mgr.setup({ pythonCmd: det.cmd, home: HOME2, srcDir: SRC, rtDir: RT });
  assert(r.ok && r.bundled, "setup 应短路: " + JSON.stringify(r));
  r = await mgr.start({ home: HOME2, srcDir: SRC, rtDir: RT, port: 18200, healthTimeoutMs: 15000 });
  assert(r.ok && r.url === "http://127.0.0.1:18200", JSON.stringify(r));
  assert.strictEqual(mgr.status(HOME2).mode, "bundled");
  assert(fs.existsSync(path.join(HOME2, "jwt.secret")), "JWT 密钥应持久化");
  mgr.stop();
  await new Promise((res) => setTimeout(res, 300));
  ok("内置运行时零环境直启 + JWT 密钥注入持久化");

  console.log(`backendmgr 冒烟：${pass}/14 全部通过`);
  fs.rmSync(TMP, { recursive: true, force: true });
}

main().catch((e) => { console.error("FAIL:", e.stack || e.message); process.exit(1); });
