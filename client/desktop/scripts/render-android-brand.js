"use strict";

// Deterministically render Observer V4 legacy Android launcher assets. Modern
// Android launchers use the adaptive foreground/background vectors; these PNGs
// cover Android 8 and OEM surfaces that still request density-specific mipmaps.
const fs = require("fs");
const path = require("path");
const sharp = require(path.resolve("frontend-next/node_modules/sharp"));

const densities = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 };
const tile = path.resolve("desktop/assets/brand/hashmm-app-tile.svg");
const output = path.resolve("tmp/observer-v4-android/res");
const mark = fs.readFileSync(path.resolve("desktop/assets/brand/hashmm-mark.svg"), "utf8")
  .replace(/<\?xml[^>]*>/g, "")
  .replace(/^<svg[^>]*>/, "")
  .replace(/<\/svg>\s*$/, "");
const round = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><circle cx="128" cy="128" r="119" fill="#f4f3ef"/><g transform="translate(0 0) scale(4)">${mark}</g></svg>`);

async function main() {
  for (const [density, size] of Object.entries(densities)) {
    const dir = path.join(output, `mipmap-${density}`);
    fs.mkdirSync(dir, { recursive: true });
    await sharp(tile).resize(size, size).png().toFile(path.join(dir, "ic_launcher.png"));
    await sharp(round).resize(size, size).png().toFile(path.join(dir, "ic_launcher_round.png"));
  }
  process.stdout.write(`rendered Android Observer V4 launcher mipmaps: ${Object.keys(densities).length * 2}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
