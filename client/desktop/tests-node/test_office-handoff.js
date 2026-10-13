"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { OfficeHandoffRegistry, safeOfficeName, mimeFor } = require("../services/office-handoff");

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-office-handoff-"));
  const file = path.join(dir, "report.docx");
  fs.writeFileSync(file, Buffer.from("first"));
  let opened = "";
  const registry = new OfficeHandoffRegistry({ openPath: async (p) => { opened = p; return ""; }, maxBytes: 1024 });
  const handoff = await registry.open(file);
  assert.strictEqual(handoff.ok, true);
  assert.strictEqual(opened, file);
  assert.strictEqual(handoff.revision, 1);
  assert.match(handoff.sha256, /^[a-f0-9]{64}$/);
  assert.strictEqual("path" in handoff, false);
  assert.strictEqual(handoff.harness.schema, "hashmm.application-harness.v1");
  assert.strictEqual(handoff.harness.sessionId, handoff.id);
  assert.strictEqual(handoff.harness.state, "active");
  assert.strictEqual(handoff.harness.integrity.arbitraryFileAccess, false);
  assert.strictEqual(handoff.harness.integrity.arbitraryCommandExecution, false);
  assert.strictEqual(registry.status(handoff.id).changed, false);

  await new Promise((r) => setTimeout(r, 8));
  fs.writeFileSync(file, Buffer.from("changed-office-file"));
  const changed = registry.status(handoff.id);
  assert.strictEqual(changed.changed, true);
  assert.strictEqual(changed.revision, 2);
  assert.strictEqual(changed.harness.state, "changed");
  const read = registry.read(handoff.id);
  assert.strictEqual(read.ok, true);
  assert.strictEqual(read.revision, 2);
  assert.match(read.sha256, /^[a-f0-9]{64}$/);
  assert.strictEqual(Buffer.from(read.dataUrl.split(",")[1], "base64").toString(), "changed-office-file");

  // A save that happens after the renderer read must not be acknowledged as
  // the uploaded revision. The user can safely retry with the latest bytes.
  await new Promise((r) => setTimeout(r, 8));
  fs.writeFileSync(file, Buffer.from("newer-office-file"));
  const conflict = registry.acknowledge(handoff.id, read.sha256);
  assert.strictEqual(conflict.ok, false);
  assert.strictEqual(conflict.conflict, true);
  assert.strictEqual(registry.status(handoff.id).changed, true);

  const latest = registry.read(handoff.id);
  assert.strictEqual(Buffer.from(latest.dataUrl.split(",")[1], "base64").toString(), "newer-office-file");
  const committed = registry.acknowledge(handoff.id, latest.sha256);
  assert.strictEqual(committed.ok, true);
  assert.strictEqual(committed.harness.state, "committed");
  assert.strictEqual(registry.status(handoff.id).changed, false);

  // Metadata-only saves do not create a fake content revision.
  const acceptedRevision = registry.status(handoff.id).revision;
  await new Promise((r) => setTimeout(r, 8));
  const now = new Date();
  fs.utimesSync(file, now, now);
  const touched = registry.status(handoff.id);
  assert.strictEqual(touched.changed, false);
  assert.strictEqual(touched.revision, acceptedRevision);

  assert.strictEqual(registry.acknowledge(handoff.id, "not-a-digest").ok, false);

  assert.throws(() => safeOfficeName("payload.exe"), /DOCX/);
  assert.strictEqual(mimeFor("slides.pptx").includes("presentation"), true);
  assert.strictEqual(registry.read("unknown").ok, false);
  fs.rmSync(dir, { recursive: true, force: true });
  console.log("office-handoff: ok");
})().catch((e) => { console.error(e); process.exitCode = 1; });
