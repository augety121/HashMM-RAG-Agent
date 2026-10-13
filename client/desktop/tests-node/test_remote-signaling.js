// desktop/tests-node/test_remote-signaling.js
// WebRTC「无服务器」信令中继的回环端到端测试（不碰真实 RTCPeerConnection——那需 Chromium，
// 这里只验服务端在 viewer ⇄ host 之间转发 offer/answer/ICE 的中继管线是否正确）：
//   · hello 下发 ICE 配置
//   · host 凭 token 注册（对/错 token 行为）
//   · viewer 配对成功 → host 收到 viewerJoined{vid}
//   · viewer→host、host→指定 viewer 的 rtcSignal 双向中继
//   · viewer 掉线 → host 收到 viewerLeft{vid}
//   · GET / 吐出查看端网页
"use strict";

const assert = require("assert");
const http = require("http");
const crypto = require("crypto");
const { RemoteServer, acceptKey, decodeFrames } = require("../services/remote-server");
const { PairingManager, TrustedDeviceStore } = require("../services/remote-pairing");

let passed = 0;
const ok = (n) => { passed++; console.log("  ✓ " + n); };

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

// 建一个最小 WS 客户端，返回 { sock, msgs(text JSON 累积), send, waitFor, closed }
function connectWS(port) {
  return new Promise((resolve, reject) => {
    const key = crypto.randomBytes(16).toString("base64");
    let headBuf = Buffer.alloc(0);
    const req = http.request({ host: "127.0.0.1", port, path: "/", headers: {
      Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Key": key, "Sec-WebSocket-Version": "13",
    }});
    req.on("upgrade", (res, sock, head) => {
      assert.strictEqual(res.headers["sec-websocket-accept"], acceptKey(key), "accept 正确");
      if (head && head.length) headBuf = Buffer.from(head);
      const msgs = [];
      const waiters = [];
      let cbuf = headBuf;
      let closed = false;
      const drain = () => {
        const { frames, rest } = decodeFrames(cbuf); cbuf = rest;
        for (const f of frames) {
          if (f.opcode === 0x1) { try { msgs.push(JSON.parse(f.payload.toString("utf8"))); } catch (_e) {} }
        }
        waiters.splice(0).forEach(fn => fn());
      };
      sock.on("data", (chunk) => { cbuf = Buffer.concat([cbuf, chunk]); drain(); });
      sock.on("close", () => { closed = true; });
      if (cbuf.length) drain();
      const send = (obj) => sock.write(encodeMasked(JSON.stringify(obj)));
      const waitFor = (pred, ms = 1500) => new Promise((res2, rej2) => {
        const t0 = Date.now();
        const check = () => {
          const v = pred();
          if (v) return res2(v);
          if (Date.now() - t0 > ms) return rej2(new Error("超时: " + pred.toString().slice(0, 70)));
          waiters.push(check); setTimeout(check, 20);
        };
        check();
      });
      resolve({ sock, msgs, send, waitFor, isClosed: () => closed });
    });
    req.on("error", reject);
    req.end();
  });
}

function httpGet(port, path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path }, (res) => {
      let body = ""; res.on("data", (d) => body += d); res.on("end", () => resolve({ status: res.statusCode, body }));
    }).on("error", reject);
  });
}

(async function run() {
  const injected = [];
  const pm = new PairingManager({ codeGen: () => "424242", maxAttempts: 5 });
  let trustedRecords = {};
  const trustedDevices = new TrustedDeviceStore({
    load: () => trustedRecords,
    save: (next) => { trustedRecords = JSON.parse(JSON.stringify(next)); },
  });
  const srv = new RemoteServer({
    pairing: pm,
    trustedDevices,
    fps: 30,
    hostToken: "HOSTTOK",
    iceServers: [{ urls: "stun:stun.example:3478" }],
    viewerHtml: "<!doctype html><title>VIEWER-PAGE</title>",
    captureFrame: async () => Buffer.from([0xff, 0xd8, 0xff, 0xe0]),
    injectInput: (ev) => injected.push(ev),
    frameMeta: () => ({ sw: 1920, sh: 1080, fw: 1280, fh: 720 }),
    name: "TestHost",
  });
  const { ok: started, port } = await srv.start(0, "127.0.0.1");
  assert.ok(started && port > 0, "服务已启动");
  srv.issueCode();

  // 1) GET / 吐查看端网页
  const page = await httpGet(port, "/");
  assert.strictEqual(page.status, 200);
  assert.ok(page.body.includes("VIEWER-PAGE"), "返回查看端 HTML");
  ok("GET / 吐出查看端网页（控制方浏览器免安装打开）");

  // 2) viewer 连上 → hello 带 ICE 配置
  const viewer = await connectWS(port);
  const hello = await viewer.waitFor(() => viewer.msgs.find(m => m.type === "hello"));
  assert.strictEqual(hello.needPair, true);
  assert.ok(Array.isArray(hello.iceServers) && hello.iceServers[0].urls === "stun:stun.example:3478", "hello 含 ICE 配置");
  ok("hello 下发 ICE 配置（viewer 据此建 RTCPeerConnection）");

  // 3) viewer 配对
  viewer.send({ type: "pair", code: "424242", deviceId: "viewer-device-0001" });
  const firstPair = await viewer.waitFor(() => viewer.msgs.find(m => m.type === "paired"));
  assert.ok(firstPair.trustToken, "first pairing issues a persistent trust credential");
  ok("viewer 配对成功");

  // 4) host 用错 token 注册 → 被拒
  const badHost = await connectWS(port);
  await badHost.waitFor(() => badHost.msgs.find(m => m.type === "hello"));
  badHost.send({ type: "hostRegister", token: "WRONG" });
  await badHost.waitFor(() => badHost.msgs.find(m => m.type === "hostRejected"));
  ok("host 错 token 注册被拒（远程 viewer 无法冒充投屏端）");

  // 5) host 用对 token 注册 → hostReady，且补发已配对 viewer 的 viewerJoined
  const host = await connectWS(port);
  await host.waitFor(() => host.msgs.find(m => m.type === "hello"));
  host.send({ type: "hostRegister", token: "HOSTTOK" });
  const ready = await host.waitFor(() => host.msgs.find(m => m.type === "hostReady"));
  assert.ok(Array.isArray(ready.iceServers), "hostReady 含 ICE");
  const vj = await host.waitFor(() => host.msgs.find(m => m.type === "viewerJoined"));
  assert.ok(typeof vj.vid === "number", "viewerJoined 带 vid");
  ok("host 对 token 注册成功 + 补发已在线 viewer 的 viewerJoined{vid}");
  const vid = vj.vid;

  // 6) viewer → host 的 rtcSignal 中继（带上 viewer 的 vid）
  viewer.send({ type: "rtcSignal", kind: "answer", data: { sdp: "A" } });
  const sigToHost = await host.waitFor(() => host.msgs.find(m => m.type === "rtcSignal" && m.kind === "answer"));
  assert.strictEqual(sigToHost.vid, vid, "中继到 host 时带正确 vid");
  assert.strictEqual(sigToHost.data.sdp, "A", "payload 透传");
  ok("viewer→host 的 answer/ICE 被正确中继（标注来源 vid）");

  // 7) host → 指定 viewer 的 rtcSignal 中继
  host.send({ type: "rtcSignal", vid, kind: "offer", data: { sdp: "O" } });
  const sigToViewer = await viewer.waitFor(() => viewer.msgs.find(m => m.type === "rtcSignal" && m.kind === "offer"));
  assert.strictEqual(sigToViewer.data.sdp, "O", "payload 透传");
  ok("host→指定 viewer 的 offer/ICE 被正确中继");

  // 8) 第二个 viewer 配对后，host 实时收到新的 viewerJoined（不同 vid）
  const viewer2 = await connectWS(port);
  await viewer2.waitFor(() => viewer2.msgs.find(m => m.type === "hello"));
  viewer2.send({ type: "pair", code: "424242" });
  // 一次性码已被 viewer1 消费 → viewer2 实际会 pairFail；为测 viewerJoined，另发一码
  // （这里直接再签发并让 viewer2 用新码）
  const code2 = srv.issueCode().code;
  viewer2.send({ type: "pair", code: code2 });
  const vj2 = await host.waitFor(() => host.msgs.find(m => m.type === "viewerJoined" && m.vid !== vid));
  assert.ok(vj2.vid !== vid, "新 viewer 不同 vid");
  ok("第二个 viewer 配对 → host 实时收到 viewerJoined（多 viewer 可路由）");

  // 9) viewer 掉线 → host 收到 viewerLeft{vid}
  viewer.sock.destroy();
  const left = await host.waitFor(() => host.msgs.find(m => m.type === "viewerLeft" && m.vid === vid));
  assert.strictEqual(left.vid, vid);
  ok("viewer 掉线 → host 收到 viewerLeft{vid}（可销毁对应 PC）");

  // 10) same LAN device reconnects with the stored credential and does not
  // need another pairing code. The host stores only the token digest.
  const resumed = await connectWS(port);
  await resumed.waitFor(() => resumed.msgs.find(m => m.type === "hello"));
  resumed.send({ type: "resume", deviceId: "viewer-device-0001", trustToken: firstPair.trustToken });
  const resumedPair = await resumed.waitFor(() => resumed.msgs.find(m => m.type === "paired" && m.resumed));
  assert.strictEqual(resumedPair.trusted, true);
  assert.notStrictEqual(trustedRecords["viewer-device-0001"].tokenHash, firstPair.trustToken);
  ok("验证码首次配对后建立可撤销信任，后续连接免重复输入");
  resumed.sock.destroy();

  console.log(`\n✅ remote-signaling 全部通过（共 ${passed} 项：网页托管 + ICE 下发 + host 注册 + 双向信令中继 + 多 viewer 路由 + 受信任设备恢复）`);
  try { srv.stop(); } catch (_e) {}
  process.exit(0);
})().catch((e) => { console.error("\n❌ 失败：", e && e.message); process.exit(1); });
