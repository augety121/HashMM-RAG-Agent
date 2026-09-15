/** Hidden Electron runtime smoke for V339 Browser Use + cockpit preload. */
"use strict";
const assert = require("assert");
const http = require("http");
const path = require("path");
const { app, BrowserWindow, ipcMain } = require("electron");
const BrowserUse = require("../modules/browser-use");

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

app.whenReady().then(async () => {
  let server = null, session = null, cockpit = null;
  try {
    server = http.createServer((req, res) => {
      if (req.url === "/redirect") {
        const port = server.address().port;
        res.writeHead(302, { Location: `http://localhost:${port}/` }); res.end(); return;
      }
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>Runtime smoke</title><button onclick=\"document.querySelector('#out').textContent='clicked'\">Run check</button><input type='password' placeholder='Password'><div id='out'>idle</div>");
    });
    const port = await listen(server);
    const origin = `http://127.0.0.1:${port}`;
    const allowed = (value) => { try { return new URL(value).hostname === "127.0.0.1"; } catch (_e) { return false; } };
    const events = [];
    session = new BrowserUse.BrowserSession();
    const opts = { headful: false, isNavigationAllowed: allowed, onEvent: (ev) => events.push(ev) };

    const nav = await session.perform({ action: "navigate", url: origin + "/" }, opts);
    assert.strictEqual(nav.ok, true); assert.ok(nav.image.startsWith("data:image/jpeg;base64,"));
    assert.ok(nav.text.includes("Run check"));
    const click = await session.perform({ action: "click", index: 0 }, opts);
    assert.strictEqual(click.ok, true);
    const read = await session.perform({ action: "read" }, opts);
    assert.ok(read.text.includes("clicked"));
    const ctx = session.inspectPlan({ action: "type", index: 1, text: "secret" });
    assert.strictEqual(ctx.element.type, "password");
    assert.strictEqual(BrowserUse.assessBrowserAction({ action: "type", index: 1, text: "secret" }, {}, ctx).decision, "confirm");

    const redirect = await session.perform({ action: "navigate", url: origin + "/redirect" }, opts);
    assert.strictEqual(redirect.ok, false); assert.ok(redirect.error.includes("显式 navigate"));
    assert.ok(events.some((ev) => ev.phase === "error" && ev.blocked));

    ipcMain.handle("browser:state", () => ({ ok: true, events: [], session: { active: false } }));
    ipcMain.handle("browser:clearTrace", () => ({ ok: true }));
    ipcMain.handle("browser:showControlled", () => ({ ok: false }));
    cockpit = new BrowserWindow({ show: false, webPreferences: {
      preload: path.join(__dirname, "..", "browser-preload.js"), nodeIntegration: false, contextIsolation: true, sandbox: true,
    } });
    await cockpit.loadFile(path.join(__dirname, "..", "browser-cockpit.html"));
    const bridge = await cockpit.webContents.executeJavaScript("({has:!!window.hashmmBrowserCockpit, requireType:typeof window.require, subtitle:document.getElementById('subtitle').textContent})");
    assert.strictEqual(bridge.has, true); assert.strictEqual(bridge.requireType, "undefined");
    assert.ok(bridge.subtitle.includes("安全桥"));
    console.log("test_browser_runtime: navigation/click/screenshot/redirect/cockpit bridge passed");
  } catch (e) {
    console.error(e && e.stack || e); process.exitCode = 1;
  } finally {
    try { session && session.dispose(); } catch (_e) {}
    try { cockpit && !cockpit.isDestroyed() && cockpit.destroy(); } catch (_e) {}
    try { server && server.close(); } catch (_e) {}
    app.quit();
  }
});
