"use strict";

const crypto = require("crypto");

const TERMINAL_CLOSE_STATES = new Map([
  [4401, { state: "auth_expired", errorCode: "REMOTE_AUTH_REQUIRED" }],
  [4403, { state: "rejected", errorCode: "REMOTE_AUTH_REJECTED" }],
  [4404, { state: "server_incompatible", errorCode: "REMOTE_SERVER_TOO_OLD" }],
  [4426, { state: "server_incompatible", errorCode: "REMOTE_PROTOCOL_UNSUPPORTED" }],
]);

function classifyAdmissionError(errorValue) {
  const error = String(errorValue || "remote-admission-failed");
  const statusMatch = error.match(/(?:socket-ticket|remote-bootstrap)-(\d{3})$/);
  const status = statusMatch ? Number(statusMatch[1]) : 0;
  if (status === 401) return { state: "auth_expired", retryable: false, errorCode: "REMOTE_AUTH_REQUIRED" };
  if (status === 403) return { state: "rejected", retryable: false, errorCode: "REMOTE_ACCESS_DENIED" };
  if ([400, 404, 405, 410, 426].includes(status)) {
    return { state: "server_incompatible", retryable: false, errorCode: "REMOTE_SERVER_TOO_OLD" };
  }
  if (["BOOTSTRAP_ENDPOINT_MISMATCH", "BOOTSTRAP_PATH_INVALID", "BOOTSTRAP_ENDPOINT_INVALID"].includes(error)) {
    return { state: "server_incompatible", retryable: false, errorCode: "REMOTE_BOOTSTRAP_INCOMPATIBLE" };
  }
  return { state: "error", retryable: status === 0 || status === 408 || status === 429 || status >= 500, errorCode: "REMOTE_ADMISSION_FAILED" };
}

// Keep one production WebSocket implementation across Electron/Node runtime
// upgrades.  The packaged app already ships `ws`; the global implementation is
// only a last-resort development fallback.  Tests may still inject a fake.
let RuntimeWebSocket = null;
try { RuntimeWebSocket = require("ws"); } catch (_e) { RuntimeWebSocket = globalThis.WebSocket || null; }

/**
 * Owns the authenticated account-level remote control socket in Electron's
 * main process.  Screen capture and RTCPeerConnection stay in the restricted
 * renderer; authentication, leases and reconnect policy must not depend on a
 * hidden BrowserWindow lifecycle.
 */
class RemoteHostSupervisor {
  constructor(options = {}) {
    this.fetch = options.fetchImpl || globalThis.fetch;
    this.WebSocket = options.WebSocketImpl || RuntimeWebSocket;
    this.onState = options.onState || (() => {});
    this.onMessage = options.onMessage || (() => {});
    this.log = options.log || (() => {});
    this.setTimeout = options.setTimeoutImpl || setTimeout;
    this.clearTimeout = options.clearTimeoutImpl || clearTimeout;
    this.setInterval = options.setIntervalImpl || setInterval;
    this.clearInterval = options.clearIntervalImpl || clearInterval;
    this.random = options.random || Math.random;
    this._generation = 0;
    this._stopped = true;
    this._socket = null;
    this._reconnectTimer = null;
    this._heartbeatTimer = null;
    this._authTimer = null;
    this._watchdogTimer = null;
    this._connectPromise = null;
    this._attempt = 0;
    this._config = null;
    this._traceId = "";
    this._lastInboundAt = 0;
    this._state = {
      state: "stopped", registered: false, detail: "", attemptId: "", at: Date.now(),
      authFrameSent: false, closeCode: 0, closeReason: "", transport: "websocket",
      traceId: "", errorCode: "", configRevision: "", security: null,
    };
  }

  snapshot() { return { ...this._state }; }

  _publish(state, detail = "", extra = {}) {
    this._state = {
      ...this._state,
      state,
      registered: state === "registered" || (state === "connected" && this._state.registered),
      detail: String(detail || "").slice(0, 240),
      at: Date.now(),
      ...extra,
    };
    try { this.onState(this.snapshot()); } catch (_e) {}
  }

  async start(config) {
    this._config = { ...(config || {}) };
    if (!this._traceId) this._traceId = `rt_${crypto.randomBytes(12).toString("hex")}`;
    this._stopped = false;
    if (this._connectPromise) return this._connectPromise;
    if (this._socket && (this._socket.readyState === 0 || this._socket.readyState === 1)) {
      return { ok: true, state: this._state.state, registered: this._state.registered };
    }
    this._connectPromise = this._connect().finally(() => { this._connectPromise = null; });
    return this._connectPromise;
  }

  updateToken(token) {
    if (!token || typeof token !== "string") return;
    this._config = { ...(this._config || {}), token };
    if (["auth_expired", "rejected"].includes(this._state.state) && !this._stopped) this._scheduleReconnect(0);
  }

  stop(reason = "stopped") {
    this._stopped = true;
    this._generation += 1;
    this._clearTimers();
    const socket = this._socket;
    this._socket = null;
    try { if (socket && socket.readyState < 2) socket.close(1000, "host-stop"); } catch (_e) {}
    this._publish("stopped", reason, { registered: false, attemptId: "" });
    this._traceId = "";
  }

  send(payload) {
    const socket = this._socket;
    if (!socket || socket.readyState !== 1) return false;
    try {
      socket.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      return true;
    } catch (_e) { return false; }
  }

  _clearTimers() {
    if (this._reconnectTimer) this.clearTimeout(this._reconnectTimer);
    if (this._heartbeatTimer) this.clearInterval(this._heartbeatTimer);
    if (this._authTimer) this.clearTimeout(this._authTimer);
    if (this._watchdogTimer) this.clearInterval(this._watchdogTimer);
    this._reconnectTimer = null;
    this._heartbeatTimer = null;
    this._authTimer = null;
    this._watchdogTimer = null;
  }

  _bind(socket, event, handler) {
    if (socket && typeof socket.addEventListener === "function") socket.addEventListener(event, handler);
    else if (socket && typeof socket.on === "function") socket.on(event, handler);
    else if (socket) socket[`on${event}`] = handler;
  }

  async _issueTicket(config, generation) {
    this._publish("issuing_ticket", "正在申请一次性主机凭证", { registered: false });
    const controller = new AbortController();
    const timeout = this.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await this.fetch(`${config.apiBase}/api/remote/v4/socket-ticket`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
        body: JSON.stringify({
          role: "host", device_id: config.deviceId, trace_id: this._traceId,
          protocol: "hashmm.remote.v4", endpoint_host: new URL(config.signalUrl).host,
        }),
        signal: controller.signal,
      });
      if (generation !== this._generation || this._stopped) return { ok: false, superseded: true };
      if (!response.ok) return { ok: false, error: `socket-ticket-${response.status}` };
      const body = await response.json();
      if (!body || !body.ticket) return { ok: false, error: "socket-ticket-missing" };
      const controlWss = String(body.control_wss || config.signalUrl || "");
      const endpoint = validateRemoteEndpoint(config.apiBase, controlWss);
      if (!endpoint.ok) return endpoint;
      return {
        ok: true, ticket: String(body.ticket), attemptId: String(body.attempt_id || ""),
        traceId: String(body.trace_id || this._traceId), signalUrl: endpoint.signalUrl,
        configRevision: String(body.config_revision || config.configRevision || ""),
      };
    } catch (error) {
      return { ok: false, error: error && error.name === "AbortError" ? "socket-ticket-timeout" : "socket-ticket-unavailable" };
    } finally { this.clearTimeout(timeout); }
  }

  async _connect() {
    const config = this._config || {};
    if (!config.token || !config.signalUrl || !config.apiBase || !config.deviceId) {
      this._publish("error", "remote-config-incomplete", { registered: false });
      return { ok: false, error: "remote-config-incomplete" };
    }
    if (typeof this.WebSocket !== "function" || typeof this.fetch !== "function") {
      this._publish("error", "main-process-websocket-unavailable", { registered: false });
      return { ok: false, error: "main-process-websocket-unavailable" };
    }
    const generation = ++this._generation;
    const admission = await this._issueTicket(config, generation);
    if (!admission.ok) {
      if (!admission.superseded) {
        const classification = classifyAdmissionError(admission.error);
        this._publish(classification.state, admission.error, {
          registered: false, errorCode: classification.errorCode, retryable: classification.retryable,
        });
        if (classification.retryable) this._scheduleReconnect();
      }
      return admission;
    }
    this._publish("opening_socket", "正在建立安全控制通道", {
      registered: false, attemptId: admission.attemptId, authFrameSent: false,
      traceId: admission.traceId, configRevision: admission.configRevision,
      errorCode: "", closeCode: 0, closeReason: "",
    });
    let socket;
    try {
      const target = new URL(admission.signalUrl);
      target.searchParams.set("attempt_id", admission.attemptId);
      target.searchParams.set("trace_id", admission.traceId);
      target.searchParams.set("protocol", "hashmm.remote.v4");
      socket = new this.WebSocket(target.toString());
    }
    catch (error) {
      const detail = `socket-constructor:${String(error && error.message || error).slice(0, 120)}`;
      this._publish("error", detail, { registered: false });
      this._scheduleReconnect();
      return { ok: false, error: detail };
    }
    this._socket = socket;
    this._bind(socket, "open", () => {
      if (generation !== this._generation || this._stopped) return;
      this._publish("authenticating", "安全通道已建立，正在注册主机", { registered: false });
      const authSent = this.send({
        type: "auth", ticket: admission.ticket, role: "host", deviceId: config.deviceId,
        name: config.name || "HashMM Desktop", platform: config.platform || process.platform,
        appVersion: config.appVersion || "", protocol: "hashmm.remote.v4",
        attemptId: admission.attemptId, traceId: admission.traceId,
      });
      if (!authSent) {
        this._publish("error", "host-auth-frame-send-failed", { registered: false, authFrameSent: false });
        try { socket.close(1011, "auth-send-failed"); } catch (_e) {}
        return;
      }
      this._publish("authenticating", "认证帧已发送，等待服务端注册回执", {
        registered: false, authFrameSent: true,
      });
      this._authTimer = this.setTimeout(() => {
        if (generation !== this._generation || this._state.registered) return;
        this._publish("error", "host-auth-timeout", { registered: false });
        try { socket.close(4408, "auth-timeout"); } catch (_e) {}
      }, 12000);
    });
    this._bind(socket, "message", (event) => {
      if (generation !== this._generation || this._stopped) return;
      // `ws` exposes MessageEvent.data through the prototype, while browser
      // implementations may expose it as an own property.  Supporting both is
      // required by the packaged Electron runtime and deterministic fakes.
      const raw = event && ("data" in Object(event)) ? event.data : event;
      let message;
      try { message = JSON.parse(typeof raw === "string" ? raw : Buffer.from(raw).toString("utf8")); }
      catch (_e) { return; }
      this._lastInboundAt = Date.now();
      if (message.type === "hostReady" || message.type === "authOk") {
        if (this._authTimer) this.clearTimeout(this._authTimer);
        this._authTimer = null;
        this._attempt = 0;
        this._publish("registered", "主机已注册，可接收连接请求", {
          registered: true, authFrameSent: true, closeCode: 0, closeReason: "",
          errorCode: "", security: message.security || null,
        });
        if (this._heartbeatTimer) this.clearInterval(this._heartbeatTimer);
        if (this._watchdogTimer) this.clearInterval(this._watchdogTimer);
        this._heartbeatTimer = this.setInterval(
          () => this.send({ type: "heartbeat", at: Date.now() }), 10000,
        );
        this._watchdogTimer = this.setInterval(() => {
          if (generation !== this._generation || this._stopped || !this._state.registered) return;
          if (this._lastInboundAt && Date.now() - this._lastInboundAt <= 35000) return;
          this._publish("degraded", "heartbeat-ack-timeout", { registered: false });
          try { socket.close(4410, "heartbeat-timeout"); } catch (_e) {}
        }, 5000);
      } else if (message.type === "authFail" || message.type === "hostRejected") {
        const reason = String(message.reason || "credential-rejected");
        const errorCode = String(message.errorCode || message.error_code || "REMOTE_AUTH_REJECTED").slice(0, 64);
        const state = reason.includes("token") ? "auth_expired" : "rejected";
        this._publish(state, reason, {
          registered: false, errorCode, traceId: String(message.traceId || admission.traceId),
          security: message.security || null,
        });
        try { socket.close(4403, "auth-rejected"); } catch (_e) {}
      } else if (message.type === "leaseRejected" || message.type === "deviceReplaced") {
        this._publish("reconnecting", message.type, { registered: false });
        try { socket.close(4409, message.type); } catch (_e) {}
      }
      try { this.onMessage(message); } catch (_e) {}
    });
    this._bind(socket, "error", (event = {}) => {
      if (generation !== this._generation || this._stopped) return;
      const detail = String(event.message || (event.error && event.error.message) || "signal-error").slice(0, 120);
      this._publish("degraded", `signal-error:${detail}`, { registered: false });
    });
    this._bind(socket, "close", (event = {}) => {
      if (generation !== this._generation) return;
      this._socket = null;
      if (this._heartbeatTimer) this.clearInterval(this._heartbeatTimer);
      if (this._authTimer) this.clearTimeout(this._authTimer);
      if (this._watchdogTimer) this.clearInterval(this._watchdogTimer);
      this._heartbeatTimer = null;
      this._authTimer = null;
      this._watchdogTimer = null;
      if (this._stopped) return;
      const code = Number(event.code || 0);
      const rawReason = event.reason;
      const reason = String(Buffer.isBuffer(rawReason) ? rawReason.toString("utf8") : rawReason || "").slice(0, 120);
      const terminal = TERMINAL_CLOSE_STATES.get(code);
      if (terminal) {
        this._publish(terminal.state, `signal-closed:${code}${reason ? `:${reason}` : ""}`, {
          registered: false, closeCode: code, closeReason: reason,
          errorCode: terminal.errorCode, retryable: false,
        });
        return;
      }
      this._publish("reconnecting", `signal-closed:${code || "unknown"}${reason ? `:${reason}` : ""}`, {
        registered: false, closeCode: code, closeReason: reason, retryable: true,
      });
      this._scheduleReconnect();
    });
    return {
      ok: true, state: "opening_socket", attemptId: admission.attemptId,
      traceId: admission.traceId, configRevision: admission.configRevision,
    };
  }

  _scheduleReconnect(delayOverride) {
    if (this._stopped || this._reconnectTimer) return;
    const delays = [1000, 2000, 5000, 10000, 30000];
    const base = delayOverride == null ? delays[Math.min(this._attempt++, delays.length - 1)] : delayOverride;
    const delay = Math.max(0, Math.round(base * (0.85 + this.random() * 0.3)));
    this._publish("reconnecting", `retry-in-${delay}ms`, { registered: false, retryInMs: delay });
    this._reconnectTimer = this.setTimeout(() => {
      this._reconnectTimer = null;
      if (!this._stopped) this.start(this._config).catch((error) => this.log("remote host reconnect failed", error));
    }, delay);
  }
}

function validateRemoteEndpoint(apiBaseValue, signalUrlValue) {
  try {
    const api = new URL(String(apiBaseValue || ""));
    const signal = new URL(String(signalUrlValue || ""));
    const loopback = ["127.0.0.1", "localhost", "::1"].includes(api.hostname);
    if (api.protocol !== "https:" && !loopback) return { ok: false, error: "PUBLIC_ENDPOINT_INSECURE" };
    if (signal.protocol !== "wss:" && !(loopback && signal.protocol === "ws:")) {
      return { ok: false, error: "PUBLIC_ENDPOINT_INSECURE" };
    }
    if (api.host !== signal.host) return { ok: false, error: "BOOTSTRAP_ENDPOINT_MISMATCH" };
    if (signal.pathname !== "/api/remote/v4/ws") return { ok: false, error: "BOOTSTRAP_PATH_INVALID" };
    return { ok: true, signalUrl: signal.toString() };
  } catch (_error) {
    return { ok: false, error: "BOOTSTRAP_ENDPOINT_INVALID" };
  }
}

module.exports = { RemoteHostSupervisor, validateRemoteEndpoint, classifyAdmissionError };
