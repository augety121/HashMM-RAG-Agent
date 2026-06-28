/** test_conversation-store.js — 对话历史持久化纯逻辑单测。 */
"use strict";
const assert = require("assert");
const S = require("../conversation-store.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== deriveTitle ===");
ok("取首条用户消息", S.deriveTitle([{ role: "user", content: "你好世界" }]) === "你好世界");
ok("跳过助手取用户", S.deriveTitle([{ role: "assistant", content: "x" }, { role: "user", content: "问题" }]) === "问题");
ok("长标题截断", S.deriveTitle([{ role: "user", content: "一".repeat(50) }]).length <= 31);
ok("多模态取 text", S.deriveTitle([{ role: "user", content: [{ type: "text", text: "图问" }, { type: "image_url" }] }]) === "图问");
ok("空→新对话", S.deriveTitle([]) === "新对话");
ok("无用户→新对话", S.deriveTitle([{ role: "assistant", content: "x" }]) === "新对话");

console.log("=== createConversation ===");
const c = S.createConversation([{ role: "user", content: "测试问题" }], 1000);
ok("有 id", typeof c.id === "string" && c.id.startsWith("c_"));
ok("标题派生", c.title === "测试问题");
ok("时间戳", c.createdAt === 1000 && c.updatedAt === 1000);
ok("消息保留", c.messages.length === 1);

console.log("=== upsert 插入/更新 ===");
let list = [];
list = S.upsert(list, S.createConversation([{ role: "user", content: "A" }], 1000), 1000);
ok("插入1个", list.length === 1);
const id0 = list[0].id;
list = S.upsert(list, S.createConversation([{ role: "user", content: "B" }], 2000), 2000);
ok("插入第2个", list.length === 2);
ok("最近更新在前", list[0].messages[0].content === "B");
// 更新已有
list = S.upsert(list, Object.assign({}, S.getById(list, id0), { messages: [{ role: "user", content: "A改" }] }), 3000);
ok("更新不新增", list.length === 2);
ok("更新后置顶", list[0].id === id0 && list[0].messages[0].content === "A改");
ok("无id视为新会话", S.upsert([], { messages: [{ role: "user", content: "X" }] }).length === 1);

console.log("=== remove / rename / getById ===");
ok("删除", S.remove(list, id0).length === 1);
ok("删不存在不变", S.remove(list, "nope").length === 2);
const renamed = S.rename(list, id0, "自定义标题");
ok("重命名", S.getById(renamed, id0).title === "自定义标题");
ok("重命名空→未命名", S.getById(S.rename(list, id0, "  "), id0).title === "未命名");
ok("getById 取到", S.getById(list, id0).id === id0);
ok("getById 取不到→null", S.getById(list, "nope") === null);

console.log("=== prune 裁剪 ===");
let many = [];
for (let i = 0; i < 60; i++) many.push(S.createConversation([{ role: "user", content: "m" + i }], 1000 + i));
const pruned = S.prune(many);
ok("裁剪到上限", pruned.length === S.MAX_CONVERSATIONS);
ok("保留最新", pruned[0].messages[0].content === "m59");
ok("自定义上限", S.prune(many, 5).length === 5);
ok("不足上限不变", S.prune([{ id: "x", updatedAt: 1 }], 50).length === 1);

console.log("=== serialize / deserialize ===");
const ser = S.serialize(list);
ok("序列化字符串", typeof ser === "string");
const de = S.deserialize(ser);
ok("反序列化恢复", de.length === 2 && de[0].id);
ok("坏 JSON→空", S.deserialize("{坏").length === 0);
ok("null→空", S.deserialize(null).length === 0);
ok("过滤无效会话", S.deserialize(JSON.stringify({ v: 1, conversations: [{ id: "ok", messages: [] }, { bad: 1 }] })).length === 1);

console.log("=== listSummary ===");
const sum = S.listSummary(list);
ok("摘要有标题", sum[0].title);
ok("摘要有消息数", typeof sum[0].count === "number");
ok("摘要不含完整messages", sum[0].messages === undefined);
ok("空列表→空摘要", S.listSummary([]).length === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
