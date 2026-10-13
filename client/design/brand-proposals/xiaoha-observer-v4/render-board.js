"use strict";

const path = require("path");
const sharp = require(path.resolve(__dirname, "..", "..", "..", "frontend-next", "node_modules", "sharp"));

const background = Buffer.from(`
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720">
  <rect width="1200" height="720" fill="#ecebe7"/>
  <text x="62" y="70" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="30" font-weight="700" fill="#18191c">HashMM · Observer V4</text>
  <text x="62" y="104" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="16" fill="#67676d">antenna + scarf study · head and gaze unchanged · production assets unchanged</text>
  <g fill="#fff"><rect x="54" y="138" width="340" height="508" rx="28"/><rect x="430" y="138" width="340" height="508" rx="28"/><rect x="806" y="138" width="340" height="508" rx="28"/></g>
  <rect x="54" y="138" width="340" height="508" rx="28" fill="none" stroke="#ef4148" stroke-width="3"/>
  <text x="82" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">01  Unified Motion</text>
  <text x="82" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">Recommended · one clear gesture</text>
  <text x="458" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">02  Quiet Signal</text>
  <text x="458" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">Balanced · calmer · softer ribbon</text>
  <text x="834" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">03  Soft Node</text>
  <text x="834" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">Technical · more detail at small size</text>
  <text x="82" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">16 / 32 / 64 px optical check</text>
  <text x="458" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">16 / 32 / 64 px optical check</text>
  <text x="834" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">16 / 32 / 64 px optical check</text>
</svg>`);

const composites = [{ input: background, left: 0, top: 0 }];
for (const [name, x] of [["01-unified-motion", 96], ["02-quiet-signal", 472], ["03-soft-node", 848]]) {
  composites.push({ input: path.join(__dirname, `${name}-256.png`), left: x, top: 170 });
  composites.push({ input: path.join(__dirname, `${name}-16.png`), left: x, top: 548 });
  composites.push({ input: path.join(__dirname, `${name}-32.png`), left: x + 42, top: 540 });
  composites.push({ input: path.join(__dirname, `${name}-64.png`), left: x + 102, top: 524 });
}

sharp({ create: { width: 1200, height: 720, channels: 4, background: "#ecebe7" } })
  .composite(composites)
  .png()
  .toFile(path.join(__dirname, "proposal-board.png"))
  .then(() => process.stdout.write("proposal board rendered\n"))
  .catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  });
