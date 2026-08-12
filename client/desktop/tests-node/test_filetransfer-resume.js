/** test_filetransfer-resume.js — 断点续传往返集成测试（纯逻辑）。
 *  模拟：发送方传到一半断线（接收方收到部分块）→ 重连后算缺块 → 只重传缺块 → 组装成功。
 *  验证续传协议核心：缺块计算正确、重传后能拼出完整文件、校验通过。
 */
"use strict";
const assert = require("assert");
const { ChunkAssembler, sliceChunks, planTransfer, checksum, checkPolicy } = require("../services/remote-filetransfer.js");
const ex = require("../filetransfer-extras.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

// 造一个文件，切成块
const fileBuf = Buffer.from("HashMM 断点续传测试 ".repeat(500), "utf8");  // 多块
const CHUNK = 64;
const chunks = sliceChunks(fileBuf, CHUNK);
const total = chunks.length;
const expChecksum = checksum(fileBuf);

console.log("=== 场景：传一半断线，重连续传 ===");
ok("文件切成多块", total > 5);

// 接收方：先收到前半 + 几个零散块（模拟断线前已收）
const asm = new ChunkAssembler({ id: "f1", size: fileBuf.length, totalChunks: total, expectedChecksum: expChecksum });
const gotBefore = [];
for (let i = 0; i < Math.floor(total / 2); i++) { asm.addChunk(chunks[i].seq, chunks[i].data); gotBefore.push(chunks[i].seq); }
asm.addChunk(chunks[total - 1].seq, chunks[total - 1].data); gotBefore.push(total - 1);  // 末块也先到
ok("断线前收到部分块", asm.received.size === gotBefore.length);
ok("断线前未完成", asm.assemble() === null);

// 重连：接收方算缺块（fileResumeQuery → missingChunks）
const have = [...asm.received.keys()];
const missing = ex.missingChunks(have, total);
ok("缺块数 = 总数 - 已收", missing.length === total - asm.received.size);
ok("缺块不含已收的", missing.every(seq => !have.includes(seq)));
ok("缺块含末块之前的中段", missing.includes(Math.floor(total / 2)));

// 发送方只重传缺块
for (const seq of missing) { const c = chunks.find(x => x.seq === seq); asm.addChunk(c.seq, c.data); }
ok("重传后全部收齐", asm.received.size === total);
ok("重传后缺块为空", ex.missingChunks([...asm.received.keys()], total).length === 0);

// 组装 + 校验
const full = asm.assemble();
ok("续传后能组装", full !== null);
ok("内容与原文件一致", full && Buffer.compare(full, fileBuf) === 0);
ok("校验和通过", full && checksum(full) === expChecksum);

console.log("=== 场景：接收方丢失状态（重启）→ 从头重发 ===");
// missingChunks 对一个全新（空）assembler：missing = 全部
const fresh = new ChunkAssembler({ id: "f2", size: fileBuf.length, totalChunks: total });
ok("空接收方→缺全部块", ex.missingChunks([...fresh.received.keys()], total).length === total);
// resumePlan 判定
const rp = ex.resumePlan([...fresh.received.keys()], total);
ok("resumePlan：未完成", rp.complete === false && rp.missingCount === total);

console.log("=== 场景：恰好全部收到 → 缺块为空，补 done 即可 ===");
const done = new ChunkAssembler({ id: "f3", size: fileBuf.length, totalChunks: total, expectedChecksum: expChecksum });
for (const c of chunks) done.addChunk(c.seq, c.data);
ok("全收→缺块空", ex.missingChunks([...done.received.keys()], total).length === 0);
ok("全收→resumePlan complete", ex.resumePlan([...done.received.keys()], total).complete === true);

console.log("=== 场景：内存接收器资源门 ===");
ok("256MB 文件允许", checkPolicy({ size: 256 * 1024 * 1024 }).allowed === true);
ok("超过 256MB 默认拒绝", checkPolicy({ size: 256 * 1024 * 1024 + 1 }).allowed === false);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
