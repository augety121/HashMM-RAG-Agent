"use strict";

// Render the Observer V4 production sources into the exact raster sizes used by
// Windows, Electron, the native installer and visual QA. ICO assembly is kept
// in a small Pillow helper because Sharp intentionally does not encode ICO.
const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");
const sharp = require(path.resolve("frontend-next/node_modules/sharp"));

async function render(source, outputDir, prefix, sizes) {
  fs.mkdirSync(outputDir, { recursive: true });
  await Promise.all(sizes.map((size) => sharp(source)
    .resize(size, size, { fit: "fill" })
    .png()
    .toFile(path.join(outputDir, `${prefix}-${size}.png`))));
}

async function main() {
  const tileSource = path.resolve("desktop/assets/brand/hashmm-app-tile.svg");
  const markSource = path.resolve("desktop/assets/brand/hashmm-mark.svg");
  const traySource = path.resolve("desktop/assets/brand/hashmm-tray.svg");
  const output = path.resolve("desktop/assets/brand/png");
  const appSizes = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256, 512, 1024];
  const markSizes = [16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80, 96, 128, 256, 512];
  const traySizes = [16, 20, 24, 32];

  await Promise.all([
    render(tileSource, output, "hashmm", appSizes),
    render(markSource, output, "hashmm-mark", markSizes),
    render(traySource, output, "hashmm-tray", traySizes),
  ]);

  await sharp(tileSource).resize(512, 512).png().toFile(path.resolve("desktop/icon.png"));
  await sharp(tileSource).resize(512, 512).png().toFile(path.resolve("installer-native/icon.png"));

  const bundledPython = path.resolve("desktop/runtime/python/python.exe");
  const python = process.platform === "win32" && fs.existsSync(bundledPython) ? bundledPython : "python";
  const ico = spawnSync(python, [path.resolve("desktop/scripts/build-icon-bundle.py")], {
    cwd: path.resolve("."), encoding: "utf8",
  });
  if (ico.status !== 0) throw new Error(ico.stderr || ico.stdout || "ICO assembly failed");
  process.stdout.write(`rendered Observer V4: ${appSizes.length} app, ${markSizes.length} mark, ${traySizes.length} tray sizes\n${ico.stdout}`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
