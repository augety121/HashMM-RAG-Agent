/** test_desktop_prompt.js — first-party approval protocol contract. */
"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const Prompt = require("../modules/desktop-prompt");
const { ApprovalJournal } = require("../modules/approval-journal");

let pass = 0;
const ok = (name) => { pass++; console.log("  ✔ " + name); };

{
  const p = Prompt.normalizePrompt({
    id: "request-1", kind: "privacy", title: "Browser approval",
    buttons: [
      { id: "allow", label: "Allow", tone: "primary" },
      { id: "deny", label: "Deny", tone: "danger" },
    ],
    cancelId: "deny", defaultId: "deny",
  });
  assert.strictEqual(p.cancelId, "deny");
  assert.strictEqual(p.defaultId, "deny");
  assert.strictEqual(p.source, "desktop");
  assert.strictEqual(p.taskId, "session");
  assert.strictEqual(Prompt.normalizeDecision(p, "allow"), "allow");
  assert.strictEqual(Prompt.normalizeDecision(p, "forged-choice"), "deny");
  ok("只接受请求声明的决定，未知决定回落取消项");
}

{
  const p = Prompt.normalizePrompt({
    title: "x".repeat(500), detail: "y".repeat(8000), target: "z".repeat(5000),
    buttons: Array.from({ length: 8 }, (_, i) => ({ id: `b${i}`, label: "l".repeat(100) })),
  });
  assert.strictEqual(p.title.length, 180);
  assert.strictEqual(p.detail.length, 4000);
  assert.strictEqual(p.target.length, 2048);
  assert.strictEqual(p.buttons.length, 4);
  assert.ok(p.buttons.every((b) => b.label.length <= 40));
  ok("标题、详情、目标和候选项均有硬上限");
}

{
  const p = Prompt.normalizePrompt({
    buttons: [
      { id: "same", label: "A" },
      { id: "same", label: "B" },
      { id: "bad id!", label: "C", tone: "made-up" },
    ],
    cancelId: "not-present", defaultId: "also-missing",
  });
  assert.deepStrictEqual(p.buttons.map((b) => b.id), ["same", "option-2", "option-3"]);
  assert.strictEqual(p.buttons[2].tone, "secondary");
  assert.strictEqual(p.cancelId, "option-3");
  assert.strictEqual(p.defaultId, "option-3");
  ok("重复/非法 id 与缺失取消项被确定性收紧");
}

{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-approval-"));
  const file = path.join(dir, "journal.json");
  let clock = 1000;
  const journal = new ApprovalJournal(file, { limit: 20, now: () => ++clock });
  const prompt = Prompt.normalizePrompt({
    id: "request-chat", source: "browser-use", taskId: "conversation-1", requestedAt: 900,
    title: "Browser action", buttons: [{ id: "allow", label: "Allow" }, { id: "deny", label: "Deny" }],
    cancelId: "deny", defaultId: "deny",
  });
  journal.request(prompt);
  journal.request(prompt);
  assert.strictEqual(journal.list({ status: "pending" }).length, 1);
  assert.strictEqual(journal.list({ taskId: "conversation-1" })[0].prompt.source, "browser-use");
  assert.strictEqual(journal.resolve(prompt.id, "forged", "user").decision, "deny");
  assert.strictEqual(journal.list()[0].status, "resolved");

  journal.request(Prompt.normalizePrompt({ ...prompt, id: "request-stale" }));
  const recovered = new ApprovalJournal(file, { limit: 20, now: () => ++clock });
  assert.strictEqual(recovered.interruptPending(), true);
  const stale = recovered.list().find(row => row.id === "request-stale");
  assert.strictEqual(stale.status, "interrupted");
  assert.strictEqual(stale.decision, "deny");
  fs.rmSync(dir, { recursive: true, force: true });
  ok("审批按 Chat 持久记录，重复请求不扩增，未知决定与重启恢复均 fail-closed");
}

console.log(`\ntest_desktop_prompt: ${pass} 项全部通过`);
