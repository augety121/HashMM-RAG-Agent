// desktop/services/test_remote-extensions.js — 沙箱可跑冒烟测试（node test_remote-extensions.js）。
// 覆盖 V103.51 远程能力扩展的两个纯逻辑模块：远程开机(WOL) 与 文件传输协议。
"use strict";
const assert = require("assert");
const { buildMagicPacket, normalizeMac } = require("./remote-wol.js");
const { planTransfer, sliceChunks, ChunkAssembler, checksum, checkPolicy } =
  require("./remote-filetransfer.js");

let passed = 0;
function ok(name) { passed++; console.log("  ok -", name); }

// ── WOL ───────────────────────────────────────────────────────────────────
(function wolTests() {
  const p = buildMagicPacket("AA:BB:CC:DD:EE:FF");
  assert.strictEqual(p.length, 102, "magic packet is 102 bytes");
  assert.ok(p.subarray(0, 6).every(b => b === 0xff), "first 6 bytes are 0xFF");
  assert.strictEqual(p.subarray(6, 12).toString("hex"), "aabbccddeeff", "MAC after header");
  assert.strictEqual(p.subarray(96, 102).toString("hex"), "aabbccddeeff", "MAC repeated to end");
  ok("WOL magic packet structure (6xFF + 16xMAC)");

  // accepts dash + bare formats
  assert.deepStrictEqual(normalizeMac("aa-bb-cc-dd-ee-ff"), Buffer.from([0xaa,0xbb,0xcc,0xdd,0xee,0xff]));
  assert.deepStrictEqual(normalizeMac("AABBCCDDEEFF"), Buffer.from([0xaa,0xbb,0xcc,0xdd,0xee,0xff]));
  ok("WOL MAC normalization (colon/dash/bare)");

  assert.throws(() => normalizeMac("zz:zz"), /非法 MAC/, "rejects invalid MAC");
  assert.throws(() => normalizeMac("AA:BB:CC"), /非法 MAC/, "rejects short MAC");
  ok("WOL rejects malformed MAC");
})();

// ── File transfer ───────────────────────────────────────────────────────────
(function fileTests() {
  const data = Buffer.from("HashMM-".repeat(20000));   // ~140KB
  const plan = planTransfer({ name: "doc.bin", size: data.length });
  assert.ok(plan.totalChunks >= 2, "multi-chunk plan");
  assert.ok(plan.id.startsWith("ft_"), "transfer id assigned");

  const chunks = sliceChunks(data, plan.chunkSize);
  assert.strictEqual(chunks.length, plan.totalChunks, "slice count matches plan");

  // out-of-order receive + duplicate + integrity
  const asm = new ChunkAssembler({ id: plan.id, size: plan.size,
    totalChunks: plan.totalChunks, expectedChecksum: checksum(data) });
  const order = [...chunks.keys()].sort(() => Math.random() - 0.5);
  let lastComplete = false;
  for (const i of order) lastComplete = asm.addChunk(chunks[i].seq, chunks[i].data).complete;
  assert.strictEqual(lastComplete, true, "completes after all chunks (any order)");
  const dup = asm.addChunk(chunks[0].seq, chunks[0].data);   // after done
  assert.ok(dup.complete, "post-complete add is a no-op");
  const full = asm.assemble();
  assert.ok(full.equals(data), "reassembled bytes equal original");
  ok("file transfer: out-of-order reassembly + integrity");

  // out-of-range guard
  const asm2 = new ChunkAssembler({ id: "x", size: 10, totalChunks: 1 });
  const oor = asm2.addChunk(5, Buffer.from("z"));
  assert.strictEqual(oor.outOfRange, true, "rejects out-of-range seq");
  ok("file transfer: out-of-range chunk rejected");

  // corrupted checksum throws
  const asm3 = new ChunkAssembler({ id: "y", size: data.length,
    totalChunks: plan.totalChunks, expectedChecksum: 12345 });
  for (const c of chunks) asm3.addChunk(c.seq, c.data);
  assert.throws(() => asm3.assemble(), /校验失败/, "bad checksum throws");
  ok("file transfer: checksum mismatch detected");

  // policy
  assert.strictEqual(checkPolicy({ size: 5 * 1024 * 1024 * 1024 }).allowed, false, "rejects >2GB");
  assert.strictEqual(checkPolicy({ size: 1000, }, { currentConcurrent: 5, maxConcurrent: 5 }).allowed, false, "rejects over-concurrency");
  assert.strictEqual(checkPolicy({ size: 1000 }).allowed, true, "allows normal file");
  ok("file transfer: policy limits (size + concurrency)");

  // empty file edge case
  const ep = planTransfer({ name: "empty", size: 0 });
  assert.strictEqual(ep.totalChunks, 0, "empty file → 0 chunks");
  const easm = new ChunkAssembler({ id: ep.id, size: 0, totalChunks: 0 });
  assert.strictEqual(easm.progress(), 1, "empty transfer is 100% immediately");
  ok("file transfer: empty file edge case");
})();

console.log(`\n✅ remote-extensions 全部通过（共 ${passed} 项：WOL 远程开机 + 文件传输协议）`);
