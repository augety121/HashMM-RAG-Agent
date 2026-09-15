/** desktop/shellserver.js — 本地壳层服务器（V87，Marvis「MarvisNode 网关」对应物）。
 *
 * 解决的根本问题：此前桌面壳 loadURL(后端地址) 直接加载 **远程** Web UI——
 * ① 前端版本被服务器绑架（服务器没重新 build，桌面装了新版也看不到新功能）；
 * ② 后端一关，桌面只剩一张连接页，"里面没有东西"。
 *
 * 现在：UI 打进安装包（resources/webui = frontend-next 静态导出），由本进程在
 * 127.0.0.1 上托管；所有 /api 请求**同源反向代理**到当前目标后端（远程 autodl
 * 或本机 sidecar）。前端零改动（相对路径照旧）、无 CORS、SSE 流式直通。
 *
 * 纯 Node 实现，不依赖 electron —— 可在沙箱里独立冒烟测试。
 */
"use strict";
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
  ".ttf": "font/ttf", ".map": "application/json", ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json",
};

// 这些前缀的请求走代理（与后端路由面对齐；其余全部按静态资源处理）
const PROXY_PREFIXES = ["/api/", "/health", "/desktop-updates"];

function isProxyPath(p) {
  return PROXY_PREFIXES.some((x) => p === x || p.startsWith(x)) || p === "/api";
}

/**
 * 创建壳层服务器。
 * @param {object} opts
 *   webuiDir   静态资源根（frontend-next 静态导出目录）
 *   port       监听端口（0 = 随机）
 *   log        (msg)=>void 可选
 * @returns {Promise<{port:number, origin:string, setTarget(url,token), getTarget(), close()}>}
 */
function createShellServer(opts) {
  const webuiDir = path.resolve(opts.webuiDir);
  const log = opts.log || (() => {});
  let target = null;          // { base: URL 字符串（无尾斜杠）, token: string }
  let embedProvider = null;   // V98: async(texts[]) => float[][] | null（主进程注入本地小模型嵌入）

  // V98 本地语义嵌入服务：POST /local/embed {texts:[...]} → {ok,dim,vectors}
  // 后端 sidecar 通过 HASHMM_LOCAL_EMBED_URL 调用（同机回环）。无 provider/未就绪
  // 一律 503——后端客户端把 503 当"服务未开"优雅降级，绝不影响检索主链。
  function handleEmbed(req, res) {
    const fail = (code, msg) => {
      try {
        if (!res.headersSent) res.writeHead(code, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: msg }));
      } catch (_e) { /* */ }
    };
    if (!embedProvider) return fail(503, "local embed provider unavailable");
    let body = "";
    let over = false;
    req.on("data", (c) => {
      body += c;
      if (body.length > 2 * 1024 * 1024) { over = true; try { req.destroy(); } catch (_e) {} }
    });
    req.on("end", async () => {
      if (over) return fail(413, "body too large");
      let texts;
      try { texts = (JSON.parse(body) || {}).texts; } catch (_e) { return fail(400, "bad json"); }
      if (!Array.isArray(texts) || !texts.length || texts.length > 64) return fail(400, "texts must be 1..64 items");
      texts = texts.map((t) => String(t == null ? "" : t).slice(0, 4000));
      try {
        const vectors = await embedProvider(texts);
        if (!vectors || !Array.isArray(vectors) || vectors.length !== texts.length)
          return fail(503, "provider not ready");
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, dim: (vectors[0] || []).length, vectors }));
      } catch (e) {
        fail(500, "embed failed: " + (e && e.message));
      }
    });
    req.on("error", () => fail(500, "request error"));
  }

  function serveStatic(req, res) {
    // 仅 GET/HEAD；其余方法对静态资源无意义
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "method not allowed" }));
    }
    let pathname;
    try { pathname = decodeURIComponent(new URL(req.url, "http://x").pathname); }
    catch (_e) { pathname = "/"; }

    // 路径穿越防护：解析后必须仍在 webuiDir 内
    let fp = path.normalize(path.join(webuiDir, pathname));
    if (!fp.startsWith(webuiDir)) {
      res.writeHead(403, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "forbidden" }));
    }

    let stat = null;
    try { stat = fs.statSync(fp); } catch (_e) { /* 不存在 */ }
    if (stat && stat.isDirectory()) {
      fp = path.join(fp, "index.html");
      try { stat = fs.statSync(fp); } catch (_e) { stat = null; }
    }
    if (!stat) {
      const ext = path.extname(pathname);
      if (!ext || ext === ".html") {
        // SPA 兜底：/chat/xxx、/privacy 等前端路由刷新 → index.html / 同名 .html
        const htmlAlt = ext === "" ? path.normalize(path.join(webuiDir, pathname + ".html")) : null;
        if (htmlAlt && htmlAlt.startsWith(webuiDir) && fs.existsSync(htmlAlt)) { fp = htmlAlt; }
        else { fp = path.join(webuiDir, "index.html"); }
        try { stat = fs.statSync(fp); } catch (_e) { stat = null; }
      }
      if (!stat) {
        res.writeHead(404, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ error: "not found" }));
      }
    }

    const ext = path.extname(fp).toLowerCase();
    const headers = { "Content-Type": MIME[ext] || "application/octet-stream", "Content-Length": stat.size };
    // Next 静态产物带内容哈希 → 强缓存；HTML 永远新鲜
    headers["Cache-Control"] = pathname.startsWith("/_next/static/")
      ? "public, max-age=31536000, immutable" : "no-cache";
    res.writeHead(200, headers);
    if (req.method === "HEAD") return res.end();
    fs.createReadStream(fp).on("error", () => { try { res.destroy(); } catch (_e) {} }).pipe(res);
  }

  function proxy(req, res) {
    if (!target) {
      res.writeHead(503, { "Content-Type": "application/json; charset=utf-8" });
      return res.end(JSON.stringify({ error: "未连接后端：请在连接页选择远程后端或启动本地后端" }));
    }
    let t;
    try { t = new URL(target.base); } catch (_e) {
      res.writeHead(502, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ error: "后端地址无效" }));
    }
    const mod = t.protocol === "https:" ? https : http;
    const reqPath = (req.url || "/").split("?")[0];   // V91: 认证事件观察用
    const headers = { ...req.headers };
    delete headers["host"];
    delete headers["connection"];
    headers["host"] = t.host;
    if (target.token) headers["x-hashmm-session-token"] = target.token;  // 壳侧会话门（与旧直载模式等价）

    const preq = mod.request({
      protocol: t.protocol, hostname: t.hostname,
      port: t.port || (t.protocol === "https:" ? 443 : 80),
      path: req.url, method: req.method, headers,
    }, (pres) => {
      // V91: 认证事件 —— 壳层天然看得见登录态变化，回调给桌面切换窗口模式。
      // login/me 2xx = 已登录；me 401 / logout 2xx = 未登录。前端零改动。
      if (opts.onAuth) {
        const sc = pres.statusCode || 0;
        try {
          if ((reqPath === "/api/auth/login" || reqPath === "/api/auth/me") && sc >= 200 && sc < 300) opts.onAuth("in");
          else if (reqPath === "/api/auth/me" && sc === 401) opts.onAuth("out");
          else if (reqPath === "/api/auth/logout" && sc >= 200 && sc < 300) opts.onAuth("out");
        } catch (_e) { /* 回调异常不影响代理 */ }
      }
      const h = { ...pres.headers };
      delete h["transfer-encoding"];       // node 会按需自己加，避免重复声明
      res.writeHead(pres.statusCode || 502, h);
      pres.pipe(res);                       // SSE/下载/流式：逐块直通，零缓冲
    });
    preq.setTimeout(310000, () => preq.destroy(new Error("proxy timeout")));  // 略大于前端 300s 流超时
    preq.on("error", (e) => {
      log(`[shell] 代理失败 ${req.method} ${req.url}: ${e.message}`);
      if (!res.headersSent) {
        res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ error: `后端不可达：${e.message}` }));
      } else { try { res.destroy(); } catch (_e) {} }
    });
    req.pipe(preq);                          // 请求体（上传/JSON）直通
    req.on("aborted", () => preq.destroy()); // 前端取消（停止生成）→ 上游同步断开
  }

  const server = http.createServer((req, res) => {
    try {
      const pathname = (req.url || "/").split("?")[0];
      if (pathname === "/local/embed" && req.method === "POST") return handleEmbed(req, res);  // V98 本机服务，不代理不落静态
      if (isProxyPath(pathname)) return proxy(req, res);
      return serveStatic(req, res);
    } catch (e) {
      log(`[shell] 处理异常: ${e.message}`);
      try {
        if (!res.headersSent) res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "internal" }));
      } catch (_e) { /* */ }
    }
  });
  // SSE 长连接不被 keep-alive 策略误杀
  server.keepAliveTimeout = 0;
  server.headersTimeout = 0;
  server.requestTimeout = 0;

  return new Promise((resolve, reject) => {
    // V94: 端口必须稳定！localStorage 按 origin（含端口）隔离——随机端口会让
    // 每次启动都换"新浏览器"，登录 token 全丢（真机实锤"每次都要重新登"）。
    // 固定首选端口，被占则 +1 顺延（最多 10 个），实际端口由调用方持久化复用。
    const preferred = opts.port || 17615;
    let attempt = 0;
    const tryListen = (p) => {
      server.once("error", (e) => {
        if (e && e.code === "EADDRINUSE" && attempt < 10) {
          attempt += 1; log(`[shell] 端口 ${p} 被占用，尝试 ${p + 1}`);
          tryListen(p + 1);
        } else { reject(e); }
      });
      server.listen(p, "127.0.0.1", () => {
        server.removeAllListeners("error");
        server.on("error", (e) => log(`[shell] server error: ${e.message}`));
        onListening();
      });
    };
    const onListening = () => {
      const port = server.address().port;
      log(`[shell] 本地壳层就绪 http://127.0.0.1:${port}（webui=${webuiDir}）`);
      resolve({
        port,
        origin: `http://127.0.0.1:${port}`,
        setTarget(url, token) {
          target = url ? { base: String(url).replace(/\/+$/, ""), token: token || "" } : null;
          log(`[shell] 代理目标 → ${target ? target.base : "（未连接）"}`);
        },
        getTarget() { return target ? target.base : null; },
        setEmbedProvider(fn) { embedProvider = typeof fn === "function" ? fn : null; },  // V98
        close() { try { server.close(); } catch (_e) {} },
      });
    };
    tryListen(preferred);
  });
}

module.exports = { createShellServer, isProxyPath };
