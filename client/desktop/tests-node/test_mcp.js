/** test_mcp.js — MCP 层测试（V100）。
 *  纯逻辑：协议编解码 / 工具注册表 / 白名单 TTL。
 *  端到端：spawn 真实 hashmm-mcp-server.js 子进程，跑完整 MCP 握手
 *  （initialize → initialized → tools/list → tools/call），证明协议真能跑通。
 *  运行：node desktop/mcp/test_mcp.js
 */
"use strict";
const assert = require("assert");
const P = require("../mcp/protocol");
const { ToolRegistry } = require("../mcp/tool-registry");
const { WhitelistManager } = require("../mcp/whitelist");
const { McpHost } = require("../mcp/mcp-host");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// ── 1. 协议编解码 ──
{
  const req = P.makeRequest(1, "tools/list", { a: 1 });
  assert.strictEqual(req.jsonrpc, "2.0");
  const wire = P.encode(req);
  assert.ok(wire.endsWith("\n"), "stdio 传输应行分隔");
  // 粘包/半包：两条消息 + 一条残行
  const { messages, rest } = P.decode(P.encode(req) + P.encode(P.makeNotification("x")) + '{"jsonrpc":"2.0","id":2');
  assert.strictEqual(messages.length, 2, "应解出 2 条完整消息");
  assert.ok(rest.startsWith('{"jsonrpc"'), "残行应留到下次");
  // 坏行不卡流
  const d2 = P.decode('garbage not json\n' + P.encode(req));
  assert.strictEqual(d2.messages.length, 1);
  assert.strictEqual(d2.errors.length, 1, "坏行计入 errors 但不抛");
  // 请求/通知判定
  assert.ok(P.isRequest(P.makeRequest(5, "m")) && !P.isNotification(P.makeRequest(5, "m")));
  assert.ok(P.isNotification(P.makeNotification("m")) && !P.isRequest(P.makeNotification("m")));
}
ok("协议：JSON-RPC 行分隔编解码 + 粘包/半包/坏行 + 请求/通知判定");

// ── 2. 工具注册表 ──
{
  const r = new ToolRegistry();
  r.register("sum", {
    description: "加法",
    inputSchema: { type: "object", properties: { a: { type: "number" }, b: { type: "number" } }, required: ["a", "b"] },
    handler: ({ a, b }) => ({ content: [{ type: "text", text: String(a + b) }] }),
  });
  assert.strictEqual(r.size(), 1);
  const listed = r.list();
  assert.ok(listed[0].inputSchema && !("handler" in listed[0]), "list() 不暴露 handler");
  // 校验：缺必填 / 类型错 / 合法
  assert.strictEqual(r.validate("sum", { a: 1 }).ok, false, "缺 b 应失败");
  assert.strictEqual(r.validate("sum", { a: 1, b: "x" }).ok, false, "b 类型错应失败");
  assert.strictEqual(r.validate("sum", { a: 1, b: 2 }).ok, true);
  assert.strictEqual(r.validate("nope", {}).ok, false, "未知工具应失败");
}
ok("注册表：注册/列出(不漏 handler)/必填+类型校验/未知工具");

// 注册表 call（异步）
(async () => {
  const r = new ToolRegistry();
  r.register("sum", {
    inputSchema: { type: "object", properties: { a: { type: "number" }, b: { type: "number" } }, required: ["a", "b"] },
    handler: ({ a, b }) => ({ content: [{ type: "text", text: String(a + b) }] }),
  });
  const res = await r.call("sum", { a: 2, b: 3 });
  assert.strictEqual(res.content[0].text, "5");
  await assert.rejects(() => r.call("sum", { a: 1 }), /缺少必填/);
  ok("注册表：call 校验并执行 handler");

  // ── 3. 白名单 TTL ──
  {
    const t0 = 1_000_000;
    const wl = new WhitelistManager({ ttlMs: 1000, defaultPolicy: "deny" });
    wl.setAllowed(["echo"], t0); // 显式拉取时间 = t0，便于测 TTL
    assert.strictEqual(wl.isAllowed("echo", t0), true, "清单内应允许");
    assert.strictEqual(wl.isAllowed("rm", t0), false, "清单外应拒绝");
    // 过期 → 回退 deny
    assert.strictEqual(wl.isAllowed("echo", t0 + 2000), false, "过期后回退 deny");
    assert.strictEqual(wl.needsRefresh(t0 + 2000), true, "过期应需刷新");
    // 刷新后恢复
    wl.setAllowed(["echo", "ls"], t0 + 2000);
    assert.strictEqual(wl.isAllowed("ls", t0 + 2000), true);
    assert.strictEqual(wl.needsRefresh(t0 + 2000), false);
    // allow 兜底策略
    const wl2 = new WhitelistManager({ ttlMs: 1000, defaultPolicy: "allow" });
    assert.strictEqual(wl2.isAllowed("anything", t0 + 99999), true, "allow 策略过期也放行");
  }
  ok("白名单：清单内放行/清单外拒绝/TTL 过期回退 deny/刷新恢复/allow 兜底");

  // ── 4. 端到端：spawn 真实 server 跑完整 MCP 握手 ──
  {
    const host = new McpHost({ timeoutMs: 10000 }); // 默认指向 hashmm-mcp-server.js
    const info = await host.start();
    assert.ok(info && info.name === "hashmm-mcp", "initialize 应返回 serverInfo");
    const tools = await host.listTools();
    const names = tools.map((t) => t.name);
    assert.ok(names.includes("echo") && names.includes("system_info"), "tools/list 应含内置工具");
    // 调用 echo
    const echo = await host.callTool("echo", { text: "hello-mcp" });
    assert.strictEqual(echo.content[0].text, "hello-mcp", "echo 工具应回显");
    // 调用 system_info
    const sys = await host.callTool("system_info", {});
    assert.ok(JSON.parse(sys.content[0].text).platform, "system_info 应返回平台");
    // 未知工具 → MethodNotFound（协议错误）
    await assert.rejects(() => host.callTool("definitely_not_a_tool", {}), /未知工具|error/i);
    // 受权限工具默认被拒（不在白名单）
    const denied = await host.callTool("save_file", { name: "x.md", content: "hi" });
    assert.strictEqual(denied.isError, true, "privileged 工具默认应被拒");
    assert.ok(denied.content[0].text.includes("未授权"), "应提示未授权");
    host.stop();
    ok("端到端：spawn server → initialize → tools/list → tools/call(echo/system_info) + 受权限工具默认拒");
  }

  // ── 5. 受权限工具门（直接驱动 server 模块，不经 stdio）──
  {
    const srv = require("../mcp/hashmm-mcp-server");
    assert.ok(srv.PRIVILEGED.has("save_file") && srv.PRIVILEGED.has("run_command"), "privileged 集合含重活工具");
    assert.strictEqual(srv.whitelist.isAllowed("save_file"), false, "授权前 save_file 拒");
    assert.strictEqual(srv.whitelist.isAllowed("echo"), true, "只读工具默认放行");
    srv.grant(["save_file"]); // 宿主授权
    assert.strictEqual(srv.whitelist.isAllowed("save_file"), true, "授权后 save_file 放行");
    assert.strictEqual(srv.whitelist.isAllowed("run_command"), false, "未授权的仍拒");
  }
  ok("受权限工具门：默认拒重活工具 / 放只读 / grant 后放行 / 未授权仍拒");

  console.log(`\ntest_mcp: ${pass} 项全部通过`);
})().catch((e) => { console.error("测试失败：", e); process.exit(1); });
