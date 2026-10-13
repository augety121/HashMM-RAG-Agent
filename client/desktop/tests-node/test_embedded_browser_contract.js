"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const main = fs.readFileSync(path.join(root, "main.js"), "utf8");
const preload = fs.readFileSync(path.join(root, "preload.js"), "utf8");
const chatArea = fs.readFileSync(path.join(root, "..", "frontend-next", "components", "ChatArea.tsx"), "utf8");
const browserInspector = fs.readFileSync(path.join(root, "..", "frontend-next", "components", "BrowserInspector.tsx"), "utf8");

assert.ok(preload.includes('browser:authorizeNavigation'), "preload exposes a narrow URL authorization method");
assert.ok(main.includes('persist:browser-use'), "embedded view is pinned to the isolated browser partition");
assert.ok(main.includes('new WebContentsView({'), "Chat browser uses a main-process WebContentsView");
assert.ok(main.includes('webviewTag: false'), "the unstable renderer webview surface is disabled");
assert.ok(main.includes('contents.session === session.fromPartition("persist:browser-use")'), "navigation policy recognizes every isolated browser WebContents");
assert.ok(main.includes('browser:authorizeNavigation'), "main process authorizes URLs before the renderer loads them");
assert.ok(main.includes('nodeIntegration: false') && main.includes('sandbox: true'), "untrusted pages remain sandboxed without Node");
assert.ok(preload.includes('browser:embeddedMount') && preload.includes('browser:embeddedNavigate'), "renderer receives only narrow embedded-browser commands");
assert.ok(preload.includes('browser:embeddedEvent'), "load and error state returns to the trusted Chat controls");
const desktopBridge = preload.slice(
  preload.indexOf('contextBridge.exposeInMainWorld("hashmmDesktop"'),
  preload.indexOf('contextBridge.exposeInMainWorld("hashmmTerm"'),
);
assert.ok(desktopBridge.includes('openExternal: (url) => ipcRenderer.invoke("shell:openExternal"'), "system browser action is exposed on the desktop bridge used by React");
assert.ok(main.includes('EmbeddedBrowser.launchPreferredBrowser'), "external links prefer installed Chrome and retain a system-browser fallback");
assert.ok(main.includes('BrowserPolicy.isTrustedServiceRedirect'), "approved www redirects can follow a narrowly scoped canonical service redirect");
assert.ok(main.includes('EmbeddedBrowser.describeLoadError'), "Chromium error codes are translated before they reach Chat");
assert.ok(main.includes('did-finish-load') && main.includes('_embeddedBrowserLastLoadedUrl'), "a successful final document clears stale redirect errors");
assert.ok(main.includes('command === "selection"') && main.includes('window.getSelection'), "the narrow browser bridge can capture only the visible user selection");
assert.ok(browserInspector.includes('api.embeddedMount(owner') && browserInspector.includes('api.embeddedNavigate(owner'), "Chat mounts and navigates the native browser surface");
assert.ok(browserInspector.includes('引用网页选中内容') && browserInspector.includes('untrusted_web_content: true'), "selected page text returns to Chat as explicitly untrusted context");
assert.ok(main.includes('EmbeddedBrowser.blockedNavigationState'), "rejected candidate navigation preserves a committed page");
assert.ok(!browserInspector.includes('这个网页暂时没有打开'), "a stale browser error card never covers a visible page");
assert.ok(!browserInspector.includes('用 Google Chrome 打开</button>'), "the redundant browser error-card action is removed");
assert.ok(!browserInspector.includes('createElement("webview")'), "Chat no longer creates a renderer webview tag");
assert.ok(chatArea.includes('isDesktopEnv && !rightPanelOpen'), "chat title controls only reserve window-control space when they reach the window edge");
assert.ok(!chatArea.includes('getBrowser()?.openCockpit().catch'), "Browser Use remains in the Chat workspace instead of forcing a detached cockpit");
console.log("test_embedded_browser_contract: all assertions passed");
