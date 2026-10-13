"use strict";

// The main process owns both the account-control WebSocket and the approval
// surface. Keeping the decision here avoids routing a security decision
// through the hidden capture renderer, whose lifecycle is independent from
// the durable control connection.
const ALLOWED_SCOPES = new Set([
  "view", "control", "clipboard", "file_read", "file_write", "audio", "power",
]);
const LABELS = {
  view: "查看屏幕",
  control: "操作鼠标与键盘",
  clipboard: "同步剪贴板",
  file_read: "读取文件",
  file_write: "发送文件",
  audio: "传输声音",
  power: "锁屏、重启或关机",
};

function normalizeRequest(payload) {
  const sessionId = String(payload && payload.sessionId || "").trim().slice(0, 96);
  const scopes = Array.from(new Set(
    (Array.isArray(payload && payload.scopes) ? payload.scopes : [])
      .map(String).filter((scope) => ALLOWED_SCOPES.has(scope)),
  ));
  if (!sessionId || !scopes.includes("view")) return null;
  const viewer = payload && typeof payload.viewer === "object" && payload.viewer ? payload.viewer : {};
  return {
    sessionId,
    scopes,
    viewer: {
      name: String(viewer.name || "另一台设备").slice(0, 50),
      platform: String(viewer.platform || "未知平台").slice(0, 40),
      deviceId: String(viewer.deviceId || "").slice(0, 128),
    },
  };
}

function promptSpec(request) {
  return {
    source: "remote-session",
    taskId: request.sessionId,
    kind: "remote-access",
    eyebrow: "远程连接请求",
    title: `${request.viewer.name} 想连接这台电脑`,
    message: `请求权限：${request.scopes.map((scope) => LABELS[scope]).filter(Boolean).join("、")}`,
    detail: `设备：${request.viewer.platform}。授权仅用于本次短时会话，断开、超时或撤销后立即失效。`,
    boundary: request.scopes.includes("power")
      ? "包含电源操作。批准后，对方可以在本次会话内执行锁屏、重启或关机。"
      : "没有列出的权限不会开放；账号相同也不会自动批准。",
    buttons: [
      { id: "approve", label: "允许本次连接", tone: "primary" },
      { id: "deny", label: "拒绝", tone: "secondary" },
    ],
    cancelId: "deny",
    defaultId: "deny",
  };
}

class RemoteAccessApprovalCoordinator {
  constructor({ requestPrompt, sendDecision, log = () => {} } = {}) {
    if (typeof requestPrompt !== "function" || typeof sendDecision !== "function") {
      throw new TypeError("remote approval requires requestPrompt and sendDecision");
    }
    this.requestPrompt = requestPrompt;
    this.sendDecision = sendDecision;
    this.log = log;
    this.pending = new Map();
  }

  handle(payload) {
    const request = normalizeRequest(payload);
    if (!request) return Promise.resolve({ ok: false, error: "invalid_permission_request" });
    const existing = this.pending.get(request.sessionId);
    if (existing) return existing;
    const task = this._decide(request).finally(() => this.pending.delete(request.sessionId));
    this.pending.set(request.sessionId, task);
    return task;
  }

  async _decide(request) {
    let result;
    try {
      result = await this.requestPrompt(promptSpec(request));
    } catch (error) {
      this.log("remote approval prompt failed", error);
      result = { decision: "deny", reason: "prompt-failed" };
    }
    const decision = result && result.decision === "approve" ? "approve" : "deny";
    const message = {
      type: "permissionDecision",
      sessionId: request.sessionId,
      decision,
      scopes: request.scopes,
    };
    const sent = !!this.sendDecision(message);
    this.log("remote approval resolved", {
      sessionId: request.sessionId,
      decision,
      sent,
      viewerDeviceId: request.viewer.deviceId,
    });
    return { ok: sent, decision, sessionId: request.sessionId, error: sent ? "" : "control_socket_unavailable" };
  }
}

module.exports = { RemoteAccessApprovalCoordinator, normalizeRequest, promptSpec };
