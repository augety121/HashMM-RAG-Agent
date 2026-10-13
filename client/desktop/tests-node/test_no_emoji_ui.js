/** Regression gate: packaged UI source must not contain emoji/dingbat glyphs. */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const roots = [
  path.join(ROOT, "frontend-next", "components"),
  path.join(ROOT, "frontend-next", "lib"),
  path.join(ROOT, "desktop"),
];
const allowedExt = new Set([".ts", ".tsx", ".js", ".html", ".json"]);
const skipParts = new Set(["runtime", "tests-node", "node_modules", "dist"]);
const glyph = /[\u2600-\u27BF]|\p{Extended_Pictographic}/u;
const failures = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (skipParts.has(entry.name)) continue;
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) { walk(file); continue; }
    if (/^test(?:_|-)/i.test(entry.name)) continue;
    if (!allowedExt.has(path.extname(entry.name))) continue;
    const text = fs.readFileSync(file, "utf8");
    const lines = text.split(/\r?\n/);
    lines.forEach((line, index) => {
      if (glyph.test(line)) failures.push(`${path.relative(ROOT, file)}:${index + 1}`);
    });
  }
}

roots.forEach(walk);
if (failures.length) {
  console.error("UI source contains forbidden emoji/dingbat glyphs:\n" + failures.join("\n"));
  process.exit(1);
}
console.log("UI emoji gate passed");
