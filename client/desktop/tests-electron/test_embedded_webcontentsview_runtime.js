"use strict";
const assert = require("assert");
const http = require("http");
const { app, BrowserWindow, WebContentsView } = require("electron");

async function main() {
  await app.whenReady();
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end("<!doctype html><html><body style='margin:0;background:#1267d6;color:white'><main id='proof'>HashMM embedded browser runtime proof</main></body></html>");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}/proof`;
  const win = new BrowserWindow({
    show: true, width: 640, height: 480,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  let view = null;
  try {
    await win.loadURL("data:text/html,<html><body style='margin:0;background:white'></body></html>");
    view = new WebContentsView({ webPreferences: {
      partition: "persist:browser-use-runtime-test",
      nodeIntegration: false, contextIsolation: true, sandbox: true,
      backgroundThrottling: false, navigateOnDragDrop: false,
    } });
    view.setBackgroundColor("#FFFFFFFF");
    view.setBounds({ x: 20, y: 30, width: 360, height: 240 });
    win.contentView.addChildView(view);
    await view.webContents.loadURL(url);
    win.showInactive();
    await new Promise(resolve => setTimeout(resolve, 250));
    const proof = await view.webContents.executeJavaScript("({text:document.querySelector('#proof').textContent,bg:getComputedStyle(document.body).backgroundColor})");
    assert.strictEqual(proof.text, "HashMM embedded browser runtime proof");
    assert.strictEqual(proof.bg, "rgb(18, 103, 214)");
    assert.deepStrictEqual(view.getBounds(), { x: 20, y: 30, width: 360, height: 240 });
    const image = await view.webContents.capturePage();
    assert.strictEqual(image.isEmpty(), false, "WebContentsView must render a non-empty page frame");
    assert.ok(image.getSize().width >= 300 && image.getSize().height >= 180, "captured frame must match the visible view area");
    console.log("test_embedded_webcontentsview_runtime: rendered, inspected and captured successfully");
  } finally {
    try { if (view) win.contentView.removeChildView(view); } catch (_e) {}
    try { if (view && !view.webContents.isDestroyed()) view.webContents.close(); } catch (_e) {}
    try { if (!win.isDestroyed()) win.destroy(); } catch (_e) {}
    await new Promise(resolve => server.close(resolve));
    app.quit();
  }
}

main().catch(error => { console.error(error); app.exit(1); });
