"use strict";

const path = require("path");
const sharp = require(path.resolve(__dirname, "..", "..", "..", "frontend-next", "node_modules", "sharp"));

const currentDir = __dirname;
const v3Dir = path.resolve(currentDir, "..", "xiaoha-focus-v3");
const oldIcon = path.resolve(currentDir, "..", "..", "..", "tmp", "icon-audit", "res", "mipmap-xxxhdpi-v4", "ic_launcher.png");
const source = path.join(currentDir, "02-observer-refined.svg");

async function renderSizes() {
  for (const size of [16, 32, 64, 256]) {
    await sharp(source)
      .resize(size, size, { fit: "fill" })
      .png()
      .toFile(path.join(currentDir, `02-observer-refined-${size}.png`));
  }
}

function boardBackground() {
  return Buffer.from(`
    <svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720">
      <rect width="1200" height="720" fill="#ecebe7"/>
      <text x="62" y="68" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="30" font-weight="700" fill="#18191c">HashMM · Observer V4 Detail Pass</text>
      <text x="62" y="103" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="16" fill="#67676d">same face · antenna and scarf only · production assets unchanged</text>

      <g fill="#fff">
        <rect x="54" y="138" width="340" height="508" rx="28"/>
        <rect x="430" y="138" width="340" height="508" rx="28"/>
        <rect x="806" y="138" width="340" height="508" rx="28"/>
      </g>
      <rect x="806" y="138" width="340" height="508" rx="28" fill="none" stroke="#ef4148" stroke-width="3"/>

      <text x="82" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">Original</text>
      <text x="82" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">stable axis · familiar soul</text>

      <text x="458" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">Observer V3</text>
      <text x="458" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">leaning antenna · long offset tail</text>

      <text x="834" y="442" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="23" font-weight="700" fill="#18191c">Observer V4</text>
      <text x="834" y="470" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="15" fill="#67676d">centered signal · compact scarf fold</text>

      <text x="82" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">original 192 px asset</text>
      <text x="458" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">16 / 32 / 64 px optical check</text>
      <text x="834" y="512" font-family="Segoe UI,Microsoft YaHei,sans-serif" font-size="13" fill="#96969b">16 / 32 / 64 px optical check</text>
    </svg>`);
}

async function renderBoard() {
  const old256 = await sharp(oldIcon).resize(256, 256).png().toBuffer();
  const v3 = path.join(v3Dir, "02-observer-256.png");
  const v4 = path.join(currentDir, "02-observer-refined-256.png");
  const composites = [
    { input: boardBackground(), left: 0, top: 0 },
    { input: old256, left: 96, top: 170 },
    { input: v3, left: 472, top: 170 },
    { input: v4, left: 848, top: 170 },
  ];

  for (const [prefix, x, dir] of [
    ["02-observer", 472, v3Dir],
    ["02-observer-refined", 848, currentDir],
  ]) {
    composites.push({ input: path.join(dir, `${prefix}-16.png`), left: x, top: 548 });
    composites.push({ input: path.join(dir, `${prefix}-32.png`), left: x + 42, top: 540 });
    composites.push({ input: path.join(dir, `${prefix}-64.png`), left: x + 102, top: 524 });
  }

  await sharp({ create: { width: 1200, height: 720, channels: 4, background: "#ecebe7" } })
    .composite(composites)
    .png()
    .toFile(path.join(currentDir, "comparison-board.png"));
}

renderSizes()
  .then(renderBoard)
  .then(() => process.stdout.write("Observer V4 assets rendered\n"))
  .catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  });

