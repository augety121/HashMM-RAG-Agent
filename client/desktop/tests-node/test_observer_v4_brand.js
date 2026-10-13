"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const DESKTOP = path.join(__dirname, "..");
const ROOT = path.join(DESKTOP, "..");
const BRAND = path.join(DESKTOP, "assets", "brand");

function pngSize(file) {
  const data = fs.readFileSync(file);
  assert.strictEqual(data.toString("hex", 0, 8), "89504e470d0a1a0a", `${file} is not PNG`);
  return [data.readUInt32BE(16), data.readUInt32BE(20)];
}

function icoSizes(file) {
  const data = fs.readFileSync(file);
  assert.strictEqual(data.readUInt16LE(0), 0, `${file} reserved header`);
  assert.strictEqual(data.readUInt16LE(2), 1, `${file} is not ICO`);
  const count = data.readUInt16LE(4);
  const sizes = [];
  for (let i = 0; i < count; i++) {
    const offset = 6 + i * 16;
    const width = data[offset] || 256;
    const height = data[offset + 1] || 256;
    assert.strictEqual(width, height, `${file} entry ${i} must be square`);
    sizes.push(width);
  }
  return sizes;
}

const tile = fs.readFileSync(path.join(BRAND, "hashmm-app-tile.svg"), "utf8");
const mark = fs.readFileSync(path.join(BRAND, "hashmm-mark.svg"), "utf8");
const tray = fs.readFileSync(path.join(BRAND, "hashmm-tray.svg"), "utf8");
for (const source of [tile, mark, tray]) {
  assert.ok(source.includes("#18191c"), "Observer V4 ink missing");
  assert.ok(source.includes("#ef4148"), "Observer V4 signal red missing");
  assert.ok(!/cheek|腮红|#fca5a5/i.test(source), "retired cheek treatment returned");
}
assert.ok(tile.includes("#f4f3ef"), "Observer V4 tile surface missing");

for (const size of [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256, 512, 1024]) {
  assert.deepStrictEqual(
    pngSize(path.join(BRAND, "png", `hashmm-${size}.png`)),
    [size, size],
    `app raster ${size}`,
  );
}
for (const size of [16, 20, 24, 32]) {
  assert.deepStrictEqual(pngSize(path.join(BRAND, "png", `hashmm-tray-${size}.png`)), [size, size]);
}

for (const ico of [path.join(DESKTOP, "build", "icon.ico"), path.join(ROOT, "installer-native", "icon.ico")]) {
  const sizes = icoSizes(ico);
  for (const required of [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]) {
    assert.ok(sizes.includes(required), `${ico} missing ${required}px entry`);
  }
}

const main = fs.readFileSync(path.join(DESKTOP, "main.js"), "utf8");
assert.ok(main.includes("hashmm-tray-32.png"), "tray must use the tiny-size V4 master");
const qrc = fs.readFileSync(path.join(ROOT, "installer-native", "resources.qrc"), "utf8");
const cpp = fs.readFileSync(path.join(ROOT, "installer-native", "main.cpp"), "utf8");
const rc = fs.readFileSync(path.join(ROOT, "installer-native", "version.rc.in"), "utf8");
assert.ok(qrc.includes("<file>icon.ico</file>"), "native resource bundle must retain ICO");
assert.ok(cpp.includes('setWindowIcon(QIcon(":/icon.png"))'), "Qt window/taskbar icon missing");
assert.ok(/IDI_HASHMM_APP\s+ICON/.test(rc), "Windows executable icon resource missing");

console.log("test_observer_v4_brand: production SVG, raster matrix, ICO, tray and native embeddings passed");
