// desktop/tests-node/test_remote-server.js
// 远程传输核心的端到端测试：
//   A) 纯编解码：acceptKey 用 RFC6455 标准向量校验；encodeFrame/decodeFrames 往返一致（含掩码）。
//   B) loopback 集成：起 RemoteServer 于 127.0.0.1，最小 WS 客户端走完整握手 → hello →
//      错码 pairFail（且消耗尝试）→ 对码 paired+meta → 收到二进制帧 → 发输入被注入。
"use strict";

const assert = require("assert");
const http = require("http");
const crypto = require("crypto");
const { RemoteServer, acceptKey, encodeFrame, decodeFrames } = require("../services/remote-server");
const { PairingManager } = require("../services/remote-pairing");

let passed = 0;
function ok(name) { passed++; console.log("  ✓ " + name); }

// ── A) 纯编解码 ──────────────────────────────────────────────
(function testCodec() {
  // RFC6455 §1.3 标准向量
  assert.strictEqual(acceptKey("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
  ok("acceptKey 符合 RFC6455 标准向量");

  // encode→decode 往返（模拟客户端掩码后再解；这里直接验服务端编码可被自家解码器读回）
  for (const text of ["hi", JSON.stringify({ type: "pair", code: "123456" }), "x".repeat(200), "y".repeat(70000)]) {
    // 服务端编码不掩码；解码器对“无掩码”帧也应能读（我们的解码器按 MASK 位处理）
    const enc = encodeFrame(text, 0x1);
    const { frames, rest } = decodeFrames(enc);
    assert.strictEqual(rest.length, 0, "无残留");
    assert.strictEqual(frames.length, 1, "恰一帧");
    assert.strictEqual(frames[0].payload.toString("utf8"), text, "往返一致");
  }
  ok("encodeFrame/decodeFrames 往返一致（含 126/127 长度分支）");

  // 半帧累积：拆成两段喂入应只在凑齐后产出
  const whole = encodeFrame("hello-split", 0x1);
  let { frames: f1, rest: r1 } = decodeFrames(whole.slice(0, 3));
  assert.strictEqual(f1.length, 0, "半帧不产出");
  ({ frames: f1, rest: r1 } = decodeFrames(Buffer.concat([r1, whole.slice(3)])));
  assert.strictEqual(f1.length, 1);
  assert.strictEqual(f1[0].payload.toString("utf8"), "hello-split");
  ok("半帧累积：凑齐才产出");
})();

// 客户端→服务端必须掩码：构造一个掩码帧编码器（测试用）
function encodeMasked(text, opcode = 0x1) {
  const data = Buffer.from(text, "utf8");
  const len = data.length;
  let header;
  if (len < 126) { header = Buffer.alloc(2); header[1] = 0x80 | len; }
  else if (len < 65536) { header = Buffer.alloc(4); header[1] = 0x80 | 126; header.writeUInt16BE(len, 2); }
  else { header = Buffer.alloc(10); header[1] = 0x80 | 127; header.writeUInt32BE(0, 2); header.writeUInt32BE(len >>> 0, 6); }
  header[0] = 0x80 | opcode;
  const mask = crypto.randomBytes(4);
  const masked = Buffer.allocUnsafe(len);
  for (let i = 0; i < len; i++) masked[i] = data[i] ^ mask[i & 3];
  return Buffer.concat([header, mask, masked]);
}

// ── B) loopback 集成 ─────────────────────────────────────────
async function testIntegration() {
  const injected = [];
  const pm = new PairingManager({ codeGen: () => "424242", maxAttempts: 5 });
  const srv = new RemoteServer({
    pairing: pm,
    fps: 30,
    captureFrame: async () => Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]),  // 假 JPEG 头
    injectInput: (ev) => injected.push(ev),
    frameMeta: () => ({ sw: 1920, sh: 1080, fw: 1280, fh: 720 }),
    name: "TestHost",
  });
  const { ok: started, port } = await srv.start(0, "127.0.0.1");
  assert.ok(started && port > 0, "服务已启动");
  srv.issueCode();  // 宿主签发配对码（=424242）

  // 最小 WS 客户端：HTTP Upgrade 握手
  const key = crypto.randomBytes(16).toString("base64");
  let headBuf = Buffer.alloc(0);
  const sock = await new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path: "/", headers: {
      Connection: "Upgrade", Upgrade: "websocket",
      "Sec-WebSocket-Key": key, "Sec-WebSocket-Version": "13",
    }});
    req.on("upgrade", (res, socket, head) => {
      assert.strictEqual(res.headers["sec-websocket-accept"], acceptKey(key), "握手 accept 正确");
      // head 是握手响应之后紧跟的首包字节（服务端的 hello 帧很可能就在这里，
      // 跟响应头合并进了同一个 TCP 段）。真浏览器会自动缓冲它；我们也得收下。
      if (head && head.length) headBuf = Buffer.from(head);
      resolve(socket);
    });
    req.on("error", reject);
    req.end();
  });
  ok("WS 握手成功（accept 校验通过）");

  // 收帧驱动：累积缓冲 → 解析 → 派发
  const textMsgs = [];
  let binCount = 0;
  let cbuf = headBuf;  // 用 head 里捎带的首帧字节作为起点
  const waiters = [];
  const drain = () => {
    const { frames, rest } = decodeFrames(cbuf); cbuf = rest;
    for (const f of frames) {
      if (f.opcode === 0x2) binCount++;
      else if (f.opcode === 0x1) { try { textMsgs.push(JSON.parse(f.payload.toString("utf8"))); } catch (_e) {} }
    }
    waiters.splice(0).forEach(fn => fn());
  };
  sock.on("data", (chunk) => { cbuf = Buffer.concat([cbuf, chunk]); drain(); });
  if (cbuf.length) drain();  // head 里若已捎带 hello 帧，先解析一次
  const send = (obj) => sock.write(encodeMasked(JSON.stringify(obj)));
  const waitFor = (pred, ms = 1500) => new Promise((resolve, reject) => {
    const t0 = Date.now();
    const check = () => {
      const v = pred();
      if (v) return resolve(v);
      if (Date.now() - t0 > ms) return reject(new Error("等待超时: " + pred.toString().slice(0, 60)));
      waiters.push(check); setTimeout(check, 30);
    };
    check();
  });

  // 1) 连上即收到 hello{needPair:true}
  await waitFor(() => textMsgs.find(m => m.type === "hello"));
  assert.strictEqual(textMsgs.find(m => m.type === "hello").needPair, true);
  assert.strictEqual(textMsgs.find(m => m.type === "hello").name, "TestHost");
  ok("连上即收到 hello{needPair:true}");

  // 2) 错码 → pairFail（且不应开始推帧）
  send({ type: "pair", code: "000000" });
  await waitFor(() => textMsgs.find(m => m.type === "pairFail"));
  const pf = textMsgs.find(m => m.type === "pairFail");
  assert.strictEqual(pf.reason, "mismatch");
  assert.strictEqual(pf.remainingAttempts, 4);
  assert.strictEqual(binCount, 0, "未配对前不推帧");
  ok("错码 → pairFail（剩余次数=4，且不推帧）");

  // 3) 未配对发输入 → 必须被忽略
  send({ type: "input", action: "left_click", x: 500, y: 500 });
  await new Promise(r => setTimeout(r, 120));
  assert.strictEqual(injected.length, 0, "未配对的输入被忽略");
  ok("未配对的输入被忽略（安全）");

  // 4) 对码 → paired + meta
  send({ type: "pair", code: "424242" });
  await waitFor(() => textMsgs.find(m => m.type === "paired"));
  assert.ok(textMsgs.find(m => m.type === "paired").token, "下发 token");
  const meta = await waitFor(() => textMsgs.find(m => m.type === "meta"));
  assert.strictEqual(meta.sw, 1920); assert.strictEqual(meta.fw, 1280);
  ok("对码 → paired(含 token) + meta(屏幕/帧尺寸)");

  // 5) 配对后开始收到二进制帧
  await waitFor(() => binCount > 0, 1500);
  ok("配对后持续收到屏幕帧（binary JPEG）");

  // 6) 配对后发输入 → 被注入（坐标透传）
  send({ type: "input", action: "left_click", x: 250, y: 750 });
  await waitFor(() => injected.length > 0);
  assert.strictEqual(injected[0].action, "left_click");
  assert.strictEqual(injected[0].x, 250); assert.strictEqual(injected[0].y, 750);
  ok("配对后输入被注入（动作/坐标透传正确）");

  // 7) ping → pong
  send({ type: "ping", t: 12345 });
  const pong = await waitFor(() => textMsgs.find(m => m.type === "pong"));
  assert.strictEqual(pong.t, 12345);
  ok("ping → pong 心跳");

  // 8) token 一次性：配对成功后码已作废，再用同码应 pairFail(reason=none)
  send({ type: "pair", code: "424242" });
  await waitFor(() => textMsgs.filter(m => m.type === "pairFail").length >= 2);
  assert.strictEqual(textMsgs.filter(m => m.type === "pairFail").pop().reason, "none");
  ok("授权码一次性消费（防重放）");

  srv.stop();
  try { sock.destroy(); } catch (_e) {}
}

(async () => {
  try {
    await testIntegration();
    console.log("\n✅ remote-server 全部通过（共 " + passed + " 项：编解码 + loopback 握手/配对/推帧/输入/心跳/防重放）");
    process.exit(0);
  } catch (e) {
    console.error("\n❌ 失败：", e && e.message);
    console.error(e && e.stack);
    process.exit(1);
  }
})();
