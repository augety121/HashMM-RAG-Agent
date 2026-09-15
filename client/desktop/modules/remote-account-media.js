"use strict";

const crypto = require("crypto");

const MEDIA_MESSAGES = new Set([
  "authOk", "hostReady", "viewerJoined", "viewerLeft", "rtcSignal", "compatReady", "rtcOn", "rtcOff", "ticket",
]);

/**
 * At-least-once bridge from the durable main-process control socket to the
 * disposable capture renderer.  Electron IPC itself is reliable only while
 * the renderer exists; messages emitted during load/recovery used to vanish
 * and leave an approved viewer waiting forever for an offer/first frame.
 */
class RemoteMediaDeliveryBridge {
  constructor({ send, log = () => {}, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout } = {}) {
    if (typeof send !== "function") throw new TypeError("send is required");
    this.send = send;
    this.log = log;
    this.setTimeout = setTimeoutImpl;
    this.clearTimeout = clearTimeoutImpl;
    this.ready = false;
    this.pending = new Map();
    this.timer = null;
  }

  accepts(message) { return !!message && MEDIA_MESSAGES.has(String(message.type || "")); }

  setReady(ready) {
    this.ready = !!ready;
    if (this.ready) this.flush();
  }

  deliver(message) {
    if (!this.accepts(message)) return false;
    const deliveryId = `md_${crypto.randomBytes(12).toString("hex")}`;
    const envelope = { ...message, _deliveryId: deliveryId };
    this.pending.set(deliveryId, { envelope, attempts: 0, createdAt: Date.now() });
    this.flush();
    return true;
  }

  acknowledge(deliveryId, ok = true) {
    const id = String(deliveryId || "");
    if (!this.pending.has(id)) return false;
    if (ok) this.pending.delete(id);
    else this.pending.get(id).attempts = 0;
    this._schedule();
    return true;
  }

  flush() {
    if (!this.ready) return;
    for (const item of this.pending.values()) {
      if (item.attempts >= 8) continue;
      item.attempts += 1;
      try { this.send(item.envelope); }
      catch (error) { this.log("media IPC send failed", String(error && error.message || error)); }
    }
    this._schedule();
  }

  _schedule() {
    if (this.timer) this.clearTimeout(this.timer);
    this.timer = null;
    if (!this.pending.size) return;
    this.timer = this.setTimeout(() => { this.timer = null; this.flush(); }, 750);
  }

  reset() {
    this.ready = false;
    if (this.timer) this.clearTimeout(this.timer);
    this.timer = null;
    this.pending.clear();
  }
}

/** Main-process JPEG compatibility path used only until WebRTC renders. */
class NativeCompatPreview {
  constructor({ captureFrame, pushFrame, signal, log = () => {}, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout } = {}) {
    if (![captureFrame, pushFrame, signal].every((fn) => typeof fn === "function")) {
      throw new TypeError("captureFrame, pushFrame and signal are required");
    }
    this.captureFrame = captureFrame;
    this.pushFrame = pushFrame;
    this.signal = signal;
    this.log = log;
    this.setTimeout = setTimeoutImpl;
    this.clearTimeout = clearTimeoutImpl;
    this.sessions = new Map();
  }

  register(message) {
    const sessionId = String(message && message.sessionId || "");
    const ticket = String(message && message.ticket || "");
    const viewerId = String(message && message.vid || "");
    if (!sessionId || !ticket || !viewerId) return false;
    this.stopByViewer(viewerId);
    this.sessions.set(sessionId, { sessionId, ticket, viewerId, timer: null, busy: false, active: false, first: false });
    return true;
  }

  updateTicket(message) {
    const session = this.sessions.get(String(message && message.sessionId || ""));
    if (!session || !message.ticket) return false;
    session.ticket = String(message.ticket);
    return true;
  }

  start(sessionId) {
    const session = this.sessions.get(String(sessionId || ""));
    if (!session) return false;
    session.active = true;
    this._pump(session);
    return true;
  }

  stop(sessionId) {
    const session = this.sessions.get(String(sessionId || ""));
    if (!session) return false;
    session.active = false;
    if (session.timer) this.clearTimeout(session.timer);
    session.timer = null;
    return true;
  }

  stopByViewer(viewerId, remove = true) {
    const vid = String(viewerId || "");
    for (const [sid, session] of this.sessions.entries()) {
      if (session.viewerId !== vid) continue;
      this.stop(sid);
      if (remove) this.sessions.delete(sid);
    }
  }

  async _pump(session) {
    if (!session.active || session.busy) return;
    session.busy = true;
    let watching = false;
    try {
      const jpeg = await this.captureFrame();
      if (jpeg && jpeg.length) {
        const result = await this.pushFrame(session, jpeg);
        watching = !!(result && result.watching);
        if (result && result.ok && !session.first) {
          session.first = true;
          this.signal({ type: "milestone", vid: session.viewerId, sessionId: session.sessionId,
            milestone: "fallback_first_frame", detail: { stage: "compat_preview", source: "electron-main" } });
        }
      }
    } catch (error) {
      this.log("native compat frame failed", String(error && error.message || error));
    } finally {
      session.busy = false;
    }
    if (!session.active) return;
    session.timer = this.setTimeout(() => this._pump(session), watching ? 250 : 1200);
  }

  reset() {
    for (const sid of Array.from(this.sessions.keys())) this.stop(sid);
    this.sessions.clear();
  }
}

module.exports = { RemoteMediaDeliveryBridge, NativeCompatPreview, MEDIA_MESSAGES };
