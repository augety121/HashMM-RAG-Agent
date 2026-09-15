/** desktop/tests-node/test_shellserver.js — 壳层服务器冒烟（纯 node，沙箱可跑）。
 *  覆盖：静态托管（含 /_next 缓存头）、SPA 兜底、路径穿越防护、未连接 503、
 *  代理 GET/POST 体直通、SSE 分块直通、X-HashMM-Session-Token 注入。
 *  运行：node desktop/tests-node/test_shellserver.js
 */
"use strict";
const http = require("http");
const path = require("path");
const fs = require("fs");
const assert = require("assert");
const { createShellServer, isProxyPath } = require("../shellserver");

const WEBUI = path.join(__dirname, "..", "..", "frontend-next", "out");

function get(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: opts.method || "GET", headers: opts.headers || {} }, (res) => {
      const chunks = [];
      const arrivals = [];
      res.on("data", (c) => { chunks.push(c); arrivals.push(Date.now()); });
      res.on("end", () => resolve({
        status: res.statusCode, headers: res.headers,
        body: Buffer.concat(chunks).toString("utf-8"), arrivals,
      }));
    });
    req.on("error", reject);
    if (opts.body) req.write(opts.body);
    req.end();
  });
}

async function main() {
  // frontend-next/out/ 是 Next.js 构建产物（非源码）。干净源码树里不存在属正常——
  // CI/开发机应先 `npm --prefix frontend-next run build` 再跑本测试。缺产物时优雅跳过
  // （与后端 _mini_runner 对缺依赖的处理一致），不把"没 build"误报成逻辑失败。
  if (!fs.existsSync(path.join(WEBUI, "index.html"))) {
    console.log("  ⏭ 跳过 shellserver 静态托管用例：frontend-next/out/ 未构建（请先 npm run build；CI 会构建后再跑）");
    console.log("test_shellserver: SKIP（缺前端构建产物，非逻辑失败）");
    return;
  }

  // ── 模拟后端：echo / SSE / health，校验 token 头透传 ──
  let seenToken = "";
  let mockAuthed = false;
  const backend = http.createServer((req, res) => {
    seenToken = req.headers["x-hashmm-session-token"] || "";
    if (req.url === "/api/health") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end('{"ok":true}'); }
    if (req.url === "/api/auth/me") { res.writeHead(mockAuthed ? 200 : 401); return res.end("{}"); }
    if (req.url === "/api/auth/login" && req.method === "POST") { mockAuthed = true; res.writeHead(200); return res.end('{"token":"t"}'); }
    if (req.url === "/api/auth/logout" && req.method === "POST") { mockAuthed = false; res.writeHead(200); return res.end("{}"); }
    if (req.url === "/api/echo" && req.method === "POST") {
      let b = ""; req.on("data", (c) => b += c);
      req.on("end", () => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify({ got: b, auth: req.headers["authorization"] || "" })); });
      return;
    }
    if (req.url === "/api/sse") {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      let n = 0;
      const t = setInterval(() => {
        res.write(`data: chunk${++n}\n\n`);
        if (n === 3) { clearInterval(t); res.end(); }
      }, 120);
      return;
    }
    res.writeHead(404); res.end();
  });
  await new Promise((r) => backend.listen(0, "127.0.0.1", r));
  const bport = backend.address().port;

  const authEvents = [];
  const shell = await createShellServer({ webuiDir: WEBUI, log: () => {},
    onAuth: (st) => authEvents.push(st) });
  const O = shell.origin;
  let pass = 0;
  const ok = (name) => { pass++; console.log("  ✔ " + name); };

  // 1. isProxyPath 路由面
  assert(isProxyPath("/api/health") && isProxyPath("/api") && isProxyPath("/desktop-updates/latest.yml"));
  assert(!isProxyPath("/apifoo") && !isProxyPath("/") && !isProxyPath("/_next/static/x.js"));
  ok("代理路由面判定");

  // 2. 静态首页
  let r = await get(O + "/");
  assert.strictEqual(r.status, 200);
  assert(/text\/html/.test(r.headers["content-type"]) && r.body.includes("<div"), "首页应为 HTML");
  ok("静态首页 200");

  // 3. SPA 兜底（前端路由刷新）
  r = await get(O + "/chat/abc123");
  assert.strictEqual(r.status, 200);
  assert(/text\/html/.test(r.headers["content-type"]));
  ok("SPA 兜底 /chat/* → index.html");

  // 4. 静态 .html 直出（privacy 页是 next export 产物）
  r = await get(O + "/privacy");
  assert.strictEqual(r.status, 200);
  ok("无扩展名路由 → 同名 .html");

  // 5. /_next 强缓存
  const nextDir = path.join(WEBUI, "_next", "static");
  const findFile = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isFile()) return p;
      const sub = findFile(p); if (sub) return sub;
    }
    return null;
  };
  const anyAsset = findFile(nextDir);
  const rel = "/" + path.relative(WEBUI, anyAsset).split(path.sep).join("/");
  r = await get(O + rel);
  assert.strictEqual(r.status, 200);
  assert(/immutable/.test(r.headers["cache-control"] || ""), "_next 资产应 immutable");
  ok("/_next 强缓存头");

  // 6. 路径穿越防护
  r = await get(O + "/..%2f..%2f..%2fetc%2fpasswd");
  assert(r.status === 403 || r.status === 404);
  assert(!r.body.includes("root:"), "绝不能读出系统文件");
  ok("路径穿越被拦");

  // 7. 未连接 → /api 503
  r = await get(O + "/api/health");
  assert.strictEqual(r.status, 503);
  assert(r.body.includes("未连接后端"));
  ok("未连接后端 503");

  // 8. 设目标后代理 GET
  shell.setTarget(`http://127.0.0.1:${bport}`, "tok-xyz");
  r = await get(O + "/api/health");
  assert.strictEqual(r.status, 200);
  assert(r.body.includes('"ok":true'));
  assert.strictEqual(seenToken, "tok-xyz", "壳层应注入会话 token 头");
  ok("代理 GET + token 注入");

  // 9. POST 体 + Authorization 直通
  r = await get(O + "/api/echo", { method: "POST", body: '{"q":"截屏问答"}',
    headers: { "Content-Type": "application/json", "Authorization": "Bearer abc" } });
  assert.strictEqual(r.status, 200);
  const j = JSON.parse(r.body);
  assert.strictEqual(j.got, '{"q":"截屏问答"}');
  assert.strictEqual(j.auth, "Bearer abc");
  ok("代理 POST 体与鉴权头直通");

  // 10. SSE 分块直通（必须逐块到达，而非攒到最后一次性给）
  r = await get(O + "/api/sse");
  assert.strictEqual(r.status, 200);
  assert(r.body.includes("chunk1") && r.body.includes("chunk3"));
  assert(r.arrivals.length >= 2, "SSE 应分多块到达");
  assert(r.arrivals[r.arrivals.length - 1] - r.arrivals[0] >= 150, "块间应有时间差（证明未缓冲）");
  ok("SSE 流式直通（无缓冲）");

  // 11.5 V91: 认证事件序列（me 401 → out；login 200 → in；logout → out）
  authEvents.length = 0;
  shell.setTarget(`http://127.0.0.1:${bport}`, "");
  await get(O + "/api/auth/me");
  await get(O + "/api/auth/login", { method: "POST", body: "{}" });
  await get(O + "/api/auth/me");
  await get(O + "/api/auth/logout", { method: "POST" });
  assert.deepStrictEqual(authEvents, ["out", "in", "in", "out"],
    "登录态事件应为 out→in→in→out，实际 " + JSON.stringify(authEvents));
  ok("认证事件回调（登录小窗的数据源）");

  // 12. 切空目标 → 回到 503
  shell.setTarget(null);
  r = await get(O + "/api/health");
  assert.strictEqual(r.status, 503);
  ok("断开目标回 503");

  shell.close();

  // 13. V94: 固定端口（登录态持久化根基）+ 占用自动顺延
  const fixed = await createShellServer({ webuiDir: WEBUI, port: 18230, log: () => {} });
  assert.strictEqual(fixed.port, 18230, "应监听在指定端口");
  const next = await createShellServer({ webuiDir: WEBUI, port: 18230, log: () => {} });
  assert.strictEqual(next.port, 18231, "占用时应 +1 顺延，实际 " + next.port);
  next.close(); backend.close();
  ok("固定端口 + 占用顺延");

  // ── V98: POST /local/embed（本地语义嵌入服务，复用 fixed 实例） ──
  const FO = fixed.origin;
  const post = (body) => get(FO + "/local/embed", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

  // 14. 无 provider → 503（后端客户端按"服务未开"降级）
  let er = await post({ texts: ["你好"] });
  assert.strictEqual(er.status, 503, "无 provider 应 503，实际 " + er.status);
  assert.strictEqual(JSON.parse(er.body).ok, false);
  ok("/local/embed 无 provider → 503");

  // 15. 注册 provider → 返回向量；GET 该路径不被拦截（走静态 SPA 兜底而非 embed）
  fixed.setEmbedProvider(async (texts) => texts.map(t => [t.length, 1]));
  er = await post({ texts: ["abc", "你好啊"] });
  assert.strictEqual(er.status, 200);
  const ej = JSON.parse(er.body);
  assert.strictEqual(ej.ok, true);
  assert.strictEqual(ej.dim, 2);
  assert.deepStrictEqual(ej.vectors, [[3, 1], [3, 1]], "向量应与 provider 输出一致");
  const gr = await get(FO + "/local/embed");
  assert.ok(!gr.body.includes('"vectors"'), "GET 不应命中 embed 处理器（应落静态/SPA 兜底）");
  ok("/local/embed provider 生效（POST 专属）");

  // 16. 入参校验：坏 JSON 400、空/超量 texts 400、provider 返回 null → 503、注销恢复 503
  er = await post("not-json");
  assert.strictEqual(er.status, 400, "坏 JSON 应 400");
  er = await post({ texts: [] });
  assert.strictEqual(er.status, 400, "空 texts 应 400");
  er = await post({ texts: Array.from({ length: 65 }, () => "x") });
  assert.strictEqual(er.status, 400, "texts>64 应 400");
  fixed.setEmbedProvider(async () => null);
  er = await post({ texts: ["x"] });
  assert.strictEqual(er.status, 503, "provider 未就绪(null) 应 503");
  fixed.setEmbedProvider(null);
  er = await post({ texts: ["x"] });
  assert.strictEqual(er.status, 503, "注销后应回到 503");
  ok("/local/embed 入参校验 + 未就绪/注销 503");

  fixed.close();

  console.log(`shellserver 冒烟：${pass}/16 全部通过`);
}

main().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
