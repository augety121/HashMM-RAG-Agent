/** desktop/backendmgr.js — 本地 HashMM 后端 sidecar 管理器（V87）。
 *
 * Marvis 的 MarvisNode 在本机跑 KnowledgeBase 服务；HashMM 的等价物就是把
 * Python 后端（fastapi + 检索核心，**无 GPU 硬依赖**——torch/FlagEmbedding 在
 * requirements-optional 且默认不装）作为子进程起在本机：
 *
 *   检测 Python(≥3.10) → 首次初始化（venv + pip install -r requirements.txt）
 *   → 启动 uvicorn hashmm.api.server:app（cwd 指向用户可写目录，data/ 落在那里）
 *   → /api/health 就绪后由壳层代理接管 → autodl 退化为"可选的远程模式"。
 *
 * 设计：纯 Node、零 electron 依赖；spawn/spawnSync/http 全部可注入 —— 沙箱里
 * 用假 Python 脚本完整冒烟（见 desktop/tests-node/）。所有方法不抛错，
 * 失败以 {ok:false, error} 返回并写日志环。
 */
"use strict";
const path = require("path");
const fs = require("fs");

const DEFAULT_PORT = 17680;
const LOG_RING = 400;          // 日志环形缓冲行数

/** V90 真机修复：Windows embeddable Python 只要存在 python3XX._pth 就进入
 *  路径隔离模式——PYTHONPATH/PYTHONHOME 被完全无视（官方文档行为），
 *  于是内置运行时找不到 hashmm 包。根治：不再依赖 PYTHONPATH，
 *  用 -c 引导代码把源码路径显式插进 sys.path（路径经环境变量传递，
 *  规避 Windows 反斜杠转义）。venv 模式同样走这条路，两种运行时统一。
 *  纯 ASCII **单行**（Windows spawn 对含换行参数的转义不可靠），
 *  getattr 防御替代 try/except。 */
const BOOT_SNIPPET =
  "import os,sys; src=os.environ.get('HASHMM_SRC',''); " +
  "(src and src not in sys.path) and sys.path.insert(0,src); " +
  "getattr(sys.stdout,'reconfigure',lambda **k:None)(encoding='utf-8'); " +
  "getattr(sys.stderr,'reconfigure',lambda **k:None)(encoding='utf-8'); " +
  "import uvicorn; uvicorn.run('hashmm.api.server:app', host='127.0.0.1', " +
  "port=int(os.environ.get('HASHMM_PORT','17680')), log_level='info')";

class BackendManager {
  /**
   * @param {object} deps  可注入：spawn / spawnSync / httpGet / platform / log
   *   httpGet(url, timeoutMs) -> Promise<{status:number}>（默认基于 node http）
   */
  constructor(deps = {}) {
    const cp = require("child_process");
    this._spawn = deps.spawn || cp.spawn;
    this._spawnSync = deps.spawnSync || cp.spawnSync;
    this._exec = deps.exec || cp.exec;
    this._platform = deps.platform || process.platform;
    this._httpGet = deps.httpGet || defaultHttpGet;
    this._log = deps.log || (() => {});
    this._onLine = null;       // UI 日志订阅
    this._lines = [];
    this.child = null;
    this.port = DEFAULT_PORT;
    this.startedAt = 0;
    this.lastError = "";
  }

  onLog(cb) { this._onLine = cb; }
  _push(line) {
    const s = String(line).replace(/\s+$/, "");
    if (!s) return;
    this._lines.push(s);
    if (this._lines.length > LOG_RING) this._lines.splice(0, this._lines.length - LOG_RING);
    this._log(s);
    try { if (this._onLine) this._onLine(s); } catch (_e) { /* */ }
  }
  logs() { return this._lines.slice(); }

  /** 子进程统一环境：强制 Python UTF-8 模式。
   *  V88 真机修复：中文 Windows 的系统编码是 GBK——pip 读 requirements、
   *  子进程输出解码全会踩雷；PYTHONUTF8=1 让 locale.getpreferredencoding()
   *  返回 utf-8，一刀切。 */
  _pyEnv(extra) {
    return { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
             PYTHONUNBUFFERED: "1", PIP_NO_INPUT: "1", ...(extra || {}) };
  }

  /** V88 第二层防御：把源 requirements 净化成纯 ASCII 的运行时副本再交给 pip。
   *  逐行去注释/空行；剩余行若仍含非 ASCII（不该出现）→ 丢弃并告警。
   *  返回 {file, hash, count}；失败返回 {error}。 */
  sanitizeRequirements(srcFile, home) {
    try {
      const raw = fs.readFileSync(srcFile, "utf-8").replace(/^\uFEFF/, "");
      const keep = [];
      for (const line0 of raw.split(/\r?\n/)) {
        const line = line0.split("#")[0].trim();
        if (!line) continue;
        if (/[^\x20-\x7E]/.test(line)) {
          this._push(`[setup] 跳过含非 ASCII 字符的依赖行: ${line0.trim().slice(0, 60)}`);
          continue;
        }
        keep.push(line);
      }
      if (!keep.length) return { error: "requirements 净化后为空（源文件异常）" };
      fs.mkdirSync(home, { recursive: true });
      const out = path.join(home, "requirements.runtime.txt");
      fs.writeFileSync(out, keep.join("\n") + "\n", "ascii");
      const hash = require("crypto").createHash("sha1").update(keep.join("\n")).digest("hex").slice(0, 12);
      return { file: out, hash, count: keep.length };
    } catch (e) { return { error: `requirements 读取失败: ${e.message}` }; }
  }

  _flagFile(home) { return path.join(home, ".deps-ok"); }

  /** V89: 内置 Python 运行时（Marvis 的 MarvisNode 对应物）。
   *  打包机用 desktop/scripts/prepare-runtime.py 把 python-embeddable + 全部依赖
   *  预装进安装包 → 用户侧零环境零初始化，点「启动并进入」直接跑。
   *  返回内置 python 路径或 null。 */
  bundledPython(rtDir) {
    if (!rtDir) return null;
    const exe = this._platform === "win32"
      ? path.join(rtDir, "python", "python.exe")
      : path.join(rtDir, "python", "bin", "python3");
    return fs.existsSync(exe) ? exe : null;
  }

  /** V89: 本地 JWT 密钥——首次启动生成随机值并持久化到 home/jwt.secret，
   *  之后每次注入 HASHMM_JWT_SECRET。用户零配置即消除"默认密钥可伪造令牌"的安全警告，
   *  且重启不换密钥（已登录会话不失效）。 */
  _jwtSecret(home) {
    const f = path.join(home, "jwt.secret");
    try {
      const v = fs.readFileSync(f, "utf-8").trim();
      if (v.length >= 32) return v;
    } catch (_e) { /* 首次 */ }
    const v = require("crypto").randomBytes(33).toString("base64url");
    try { fs.mkdirSync(home, { recursive: true }); fs.writeFileSync(f, v + "\n"); } catch (_e) { /* */ }
    return v;
  }

  /** 启动失败时从日志环倒序抓最后一条像样的错误行，拼进人话提示。 */
  _lastErrorLine() {
    for (let i = this._lines.length - 1; i >= 0; i--) {
      const l = this._lines[i];
      if (/(Error|ERROR|Traceback|No module named|RuntimeError|Exception)/.test(l) && !/进程退出/.test(l)) {
        return l.slice(0, 160);
      }
    }
    return "";
  }

  /** 探测可用 Python（≥3.10）。custom 为用户手填路径/命令，优先尝试。 */
  detectPython(custom) {
    const candidates = [];
    if (custom && String(custom).trim()) candidates.push(splitCmd(custom));
    if (this._platform === "win32") candidates.push(["py", "-3"]);
    candidates.push(["python3"], ["python"]);
    for (const c of candidates) {
      try {
        const r = this._spawnSync(c[0], [...c.slice(1), "--version"], { encoding: "utf-8", timeout: 8000 });
        const out = `${r.stdout || ""}${r.stderr || ""}`;
        const m = out.match(/Python\s+(\d+)\.(\d+)/i);
        if (r.status === 0 && m) {
          const major = +m[1], minor = +m[2];
          if (major > 3 || (major === 3 && minor >= 10)) {
            return { ok: true, cmd: c, version: `${m[1]}.${m[2]}` };
          }
          this._push(`[python] ${c.join(" ")} 版本 ${m[1]}.${m[2]} 过低（需 ≥3.10）`);
        }
      } catch (_e) { /* 候选不存在，继续 */ }
    }
    return { ok: false, error: "未找到 Python ≥3.10（可手动填写 python.exe 完整路径）" };
  }

  /** venv 内 python 路径 */
  venvPython(home) {
    return this._platform === "win32"
      ? path.join(home, "venv", "Scripts", "python.exe")
      : path.join(home, "venv", "bin", "python");
  }

  /** V88 真机修复：venv 存在 ≠ 环境就绪（pip 半途失败时 venv 在、包不在，
   *  此前据此显示"已就绪"是撒谎）。新判定 = venv python 存在 且 .deps-ok 标记在；
   *  无标记且 fast=false 时，实测 `import fastapi, uvicorn` 一次，通过则补写标记
   *  （兼容老用户/手工装好的环境）。 */
  envReady(home, opts = {}) {
    if (opts.rtDir && this.bundledPython(opts.rtDir)) return true;   // V89: 内置运行时恒就绪
    const vp = this.venvPython(home);
    if (!fs.existsSync(vp)) return false;
    if (fs.existsSync(this._flagFile(home))) return true;
    if (opts.fast) return false;
    try {
      const r = this._spawnSync(vp, ["-c", "import fastapi, uvicorn"],
        { encoding: "utf-8", timeout: 20000, env: this._pyEnv() });
      if (r.status === 0) {
        try { fs.writeFileSync(this._flagFile(home), "verified\n"); } catch (_e) { /* */ }
        return true;
      }
    } catch (_e) { /* */ }
    return false;
  }

  /** 重置本地环境：删 venv 与就绪标记（运行中先停）。依赖损坏时的一键自愈。 */
  resetEnv(home) {
    try {
      this.stop();
      fs.rmSync(path.join(home, "venv"), { recursive: true, force: true });
      fs.rmSync(this._flagFile(home), { force: true });
      this._push("[setup] 已重置本地环境（venv 已删除），请重新初始化");
      return { ok: true };
    } catch (e) { return { ok: false, error: e.message }; }
  }

  /**
   * 首次初始化：创建 venv + 安装净化后的 requirements（约 2-5 分钟，逐行日志）。
   * 成功的定义不是 pip 退出码，而是装完后 `import fastapi, uvicorn` 实测通过。
   * @param {{pythonCmd:string[], home:string, srcDir:string, mirror?:string}} o
   */
  async setup(o) {
    try {
      if (o.rtDir && this.bundledPython(o.rtDir)) {
        this._push("[setup] 检测到内置运行时——无需初始化，直接「启动并进入」即可");
        return { ok: true, bundled: true };
      }
      fs.mkdirSync(o.home, { recursive: true });
      try { fs.rmSync(this._flagFile(o.home), { force: true }); } catch (_e) { /* */ }
      const venvDir = path.join(o.home, "venv");
      if (!fs.existsSync(this.venvPython(o.home))) {
        this._push(`[setup] 创建虚拟环境 ${venvDir}`);
        const r1 = await this._run(o.pythonCmd[0], [...o.pythonCmd.slice(1), "-m", "venv", venvDir],
                                   { env: this._pyEnv() });
        if (r1 !== 0) return { ok: false, error: "venv 创建失败（看上方日志）" };
      } else {
        this._push("[setup] 虚拟环境已存在，跳过创建");
      }
      const vp = this.venvPython(o.home);
      const srcReq = path.join(o.srcDir, "requirements.txt");
      if (!fs.existsSync(srcReq)) return { ok: false, error: `缺少 ${srcReq}` };
      // V88: 不直接喂源文件——净化成纯 ASCII 运行时副本（中文 Windows 上
      // pip 按 GBK 读 requirements，任何 UTF-8 注释都会 UnicodeDecodeError）
      const san = this.sanitizeRequirements(srcReq, o.home);
      if (san.error) return { ok: false, error: san.error };
      this._push(`[setup] 依赖清单净化完成（${san.count} 项，hash=${san.hash}）`);
      const pipArgs = ["-m", "pip", "install", "-r", san.file,
                       "--disable-pip-version-check", "--prefer-binary"];
      if (o.mirror) pipArgs.push("-i", o.mirror);
      this._push(`[setup] 安装依赖（核心轻量集，无 GPU 包）${o.mirror ? "（镜像 " + o.mirror + "）" : ""}`);
      const r2 = await this._run(vp, pipArgs, { env: this._pyEnv() });
      if (r2 !== 0) return { ok: false, error: "依赖安装失败（可在上方日志找原因；国内网络可填镜像重试）" };
      // 实测导入：pip 偶有"退出 0 但装挂"的边角，眼见为实再发就绪标记
      this._push("[setup] 校验核心包可导入…");
      const r3 = await this._run(vp, ["-c", "import fastapi, uvicorn, numpy"], { env: this._pyEnv() });
      if (r3 !== 0) return { ok: false, error: "依赖校验未通过（fastapi/uvicorn/numpy 导入失败，看上方日志）" };
      try { fs.writeFileSync(this._flagFile(o.home), `ok ${san.hash}\n`); } catch (_e) { /* */ }
      this._push("[setup] 初始化完成 ✔ 可以启动本地后端了");
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /** 跑一条命令并把输出灌进日志环，resolve 退出码 */
  _run(cmd, args, opts = {}) {
    return new Promise((resolve) => {
      let p;
      try { p = this._spawn(cmd, args, { windowsHide: true, ...opts }); }
      catch (e) { this._push(`[run] 启动失败: ${e.message}`); return resolve(-1); }
      const feed = (buf) => String(buf).split(/\r?\n/).forEach((l) => this._push(l));
      if (p.stdout) p.stdout.on("data", feed);
      if (p.stderr) p.stderr.on("data", feed);
      p.on("error", (e) => { this._push(`[run] ${e.message}`); resolve(-1); });
      p.on("exit", (code) => resolve(code == null ? -1 : code));
    });
  }

  /** V88: 端口占用自动避让——首选端口被占就 +1 往后扫（最多 10 个）。 */
  _pickPort(preferred) {
    const net = require("net");
    const tryOne = (p) => new Promise((resolve) => {
      const s = net.createServer();
      s.once("error", () => resolve(false));
      s.once("listening", () => s.close(() => resolve(true)));
      s.listen(p, "127.0.0.1");
    });
    return (async () => {
      for (let p = preferred; p < preferred + 10; p++) {
        if (await tryOne(p)) {
          if (p !== preferred) this._push(`[backend] 端口 ${preferred} 被占用，改用 ${p}`);
          return p;
        }
      }
      return 0;
    })();
  }

  /**
   * 启动本地后端并等待 /api/health 就绪。
   * @param {{home:string, srcDir:string, port?:number, env?:object}} o
   */
  async start(o) {
    if (this.child) return { ok: true, url: `http://127.0.0.1:${this.port}`, already: true };
    // V89: 内置运行时优先（零环境）；否则走 venv 路线
    const bp = this.bundledPython(o.rtDir);
    const vp = bp || this.venvPython(o.home);
    this.mode = bp ? "bundled" : "venv";
    if (!bp) {
      if (!fs.existsSync(vp)) return { ok: false, error: "本地环境未初始化（先点「初始化环境」）" };
      if (!this.envReady(o.home)) {
        return { ok: false, error: "本地环境不完整（依赖未装好）——请点「初始化环境」重新安装，装完会自动校验" };
      }
    }
    if (!fs.existsSync(path.join(o.srcDir, "hashmm"))) {
      return { ok: false, error: `后端源码缺失：${o.srcDir}/hashmm（安装包应自带，若是开发模式请确认仓库结构）` };
    }
    const picked = await this._pickPort(o.port || DEFAULT_PORT);
    if (!picked) return { ok: false, error: "17680 起连续 10 个端口都被占用，请清理后重试" };
    this.port = picked;
    fs.mkdirSync(o.home, { recursive: true });
    const env = this._pyEnv({
      PYTHONPATH: o.srcDir,                          // venv 路线的双保险（embeddable 会无视它）
      HASHMM_SRC: o.srcDir,                          // V90: 引导代码显式注入 sys.path 用
      HASHMM_PORT: String(this.port),
      HASHMM_JWT_SECRET: this._jwtSecret(o.home),   // V89: 零配置消除默认密钥告警
      // V103: 功能预设。桌面端默认 recommended，解锁已有的低风险深度（提示/管线缓存、
      // agent 时间线、工具审计、用户/记忆服务）。尊重用户已在系统环境里设的 HASHMM_PRESET；
      // 想满血（评判器/HyDE/多查询/agentic 检索）设 max，想回到旧行为设 basic。
      // 后端代码本身默认 basic，故服务器/其它部署不受此默认影响。
      HASHMM_PRESET: process.env.HASHMM_PRESET || "recommended",
      ...(o.env || {}),
    });
    const args = ["-c", BOOT_SNIPPET];
    this._push(`[backend] 启动（${this.mode === "bundled" ? "内置运行时" : "本机 venv"}）：${vp} ` +
               `→ hashmm.api.server @ 127.0.0.1:${this.port}（源码=${o.srcDir}，数据=${path.join(o.home, "data")}）`);
    try {
      this.child = this._spawn(vp, args, { cwd: o.home, env, windowsHide: true });
    } catch (e) {
      this.child = null;
      return { ok: false, error: `启动失败: ${e.message}` };
    }
    this.startedAt = Date.now();
    const feed = (buf) => String(buf).split(/\r?\n/).forEach((l) => this._push(l));
    if (this.child.stdout) this.child.stdout.on("data", feed);
    if (this.child.stderr) this.child.stderr.on("data", feed);
    this.child.on("exit", (code) => {
      this._push(`[backend] 进程退出（code=${code}）`);
      this.child = null;
    });

    const url = `http://127.0.0.1:${this.port}`;
    const ok = await this._waitHealth(url, o.healthTimeoutMs || 90000);
    if (!ok) {
      const lastErr = this._lastErrorLine();
      this.lastError = this.child
        ? "等待 /api/health 超时（看日志定位；首启建索引可能较慢，可再点一次启动）"
        : "后端进程启动即退出" + (lastErr ? `：${lastErr}` : "（看下方日志）");
      return { ok: false, error: this.lastError, url };
    }
    this._push(`[backend] 就绪 ${url}`);
    return { ok: true, url };
  }

  async _waitHealth(base, timeoutMs) {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      if (!this.child) return false;        // 进程已死，别白等
      try {
        const r = await this._httpGet(base + "/api/health", 3000);
        if (r && r.status >= 200 && r.status < 500) return true;   // 起来了（即使 401 也算活）
      } catch (_e) { /* 还没起来 */ }
      await sleep(600);
    }
    return false;
  }

  stop() {
    const c = this.child;
    if (!c) return { ok: true, already: true };
    this._push("[backend] 停止本地后端…");
    try { c.kill(); } catch (_e) { /* */ }
    if (this._platform === "win32" && c.pid) {
      // Windows 上确保整棵进程树退干净（uvicorn 子 worker）
      try { this._exec(`taskkill /PID ${c.pid} /T /F`, () => {}); } catch (_e) { /* */ }
    }
    this.child = null;
    return { ok: true };
  }

  status(home) {
    return {
      running: !!this.child,
      pid: this.child ? this.child.pid : null,
      port: this.port,
      mode: this.mode || "venv",
      url: `http://127.0.0.1:${this.port}`,
      envReady: home ? this.envReady(home, { fast: true }) : undefined,
      startedAt: this.startedAt || null,
      lastError: this.lastError || "",
    };
  }
}

function splitCmd(s) {
  // 支持 "C:\Python\python.exe" 或 "py -3" 两种填法（带空格路径加引号）
  const m = String(s).trim().match(/(?:[^\s"]+|"[^"]*")+/g) || [];
  return m.map((x) => x.replace(/^"|"$/g, ""));
}

function defaultHttpGet(url, timeoutMs) {
  const http = require("http");
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => { res.resume(); resolve({ status: res.statusCode || 0 }); });
    req.setTimeout(timeoutMs || 3000, () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

module.exports = { BackendManager, DEFAULT_PORT, splitCmd };
