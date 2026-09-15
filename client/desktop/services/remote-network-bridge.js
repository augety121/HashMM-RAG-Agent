"use strict";

/**
 * Private-network bridge for HashMM remote work.
 *
 * HashMM deliberately does not embed or silently start a mesh daemon here.
 * EasyTier (or another user-approved VPN) owns its privileged adapter, service,
 * network membership and secret.  This module only discovers a usable virtual
 * address and launches an explicit client with a validated target.
 */
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const ADAPTER_HINT = /(easytier|tailscale|zerotier|wireguard|wintun|tun|tap|vpn)/i;
const HOSTNAME = /^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i;

function normalizeTarget(value) {
  const target = String(value || "").trim();
  if (!target || target.length > 253) throw new Error("请输入私网 IP 或设备名");
  if (/[:/\\?#\s"'`;&|<>$(){}\[\]]/.test(target)) {
    throw new Error("目标只能是 IP 地址或设备名，不能包含端口、路径或命令字符");
  }
  if (net.isIP(target) === 4 || HOSTNAME.test(target)) return target;
  throw new Error("目标不是有效的 IPv4 地址或设备名");
}

function normalizePort(value, fallback = 17690) {
  const parsed = value == null || value === "" ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error("端口必须是 1 到 65535 之间的整数");
  }
  return parsed;
}

function privateAdapters(networkInterfaces = os.networkInterfaces()) {
  const rows = [];
  for (const [name, entries] of Object.entries(networkInterfaces || {})) {
    if (!ADAPTER_HINT.test(name)) continue;
    for (const entry of entries || []) {
      const family = typeof entry.family === "string" ? entry.family : (entry.family === 4 ? "IPv4" : "IPv6");
      if (family !== "IPv4" || entry.internal || !entry.address || entry.address.startsWith("169.254.")) continue;
      rows.push({ name, address: entry.address, cidr: entry.cidr || "", mac: entry.mac || "" });
    }
  }
  return rows;
}

function firstExisting(candidates) {
  for (const item of candidates.filter(Boolean)) {
    try {
      if (fs.existsSync(item) && fs.statSync(item).isFile()) return item;
    } catch (_) {
      // A missing or inaccessible optional client is represented as not found.
    }
  }
  return "";
}

function pathExecutable(names, env = process.env) {
  const dirs = String(env.PATH || "").split(path.delimiter).filter(Boolean);
  const suffixes = process.platform === "win32" ? ["", ".exe", ".cmd"] : [""];
  for (const dir of dirs) {
    for (const name of names) {
      for (const suffix of suffixes) {
        const found = firstExisting([path.join(dir, name.endsWith(suffix) ? name : name + suffix)]);
        if (found) return found;
      }
    }
  }
  return "";
}

function detectClients(env = process.env) {
  const systemRoot = env.SystemRoot || env.WINDIR || "C:\\Windows";
  const local = env.LOCALAPPDATA || "";
  const programFiles = env.ProgramFiles || "C:\\Program Files";
  const programFilesX86 = env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  const rdp = process.platform === "win32"
    ? firstExisting([path.join(systemRoot, "System32", "mstsc.exe")]) || pathExecutable(["mstsc"], env)
    : pathExecutable(["xfreerdp", "rdesktop"], env);
  const moonlight = firstExisting([
    path.join(programFiles, "Moonlight Game Streaming", "Moonlight.exe"),
    path.join(programFilesX86, "Moonlight Game Streaming", "Moonlight.exe"),
    local && path.join(local, "Moonlight Game Streaming", "Moonlight.exe"),
    local && path.join(local, "Programs", "Moonlight", "Moonlight.exe"),
  ]) || pathExecutable(["Moonlight", "moonlight"], env);
  const easytier = firstExisting([
    path.join(programFiles, "EasyTier", "easytier-core.exe"),
    path.join(programFilesX86, "EasyTier", "easytier-core.exe"),
    local && path.join(local, "EasyTier", "easytier-core.exe"),
  ]) || pathExecutable(["easytier-core", "easytier-cli"], env);
  return {
    rdp: { available: Boolean(rdp), path: rdp },
    moonlight: { available: Boolean(moonlight), path: moonlight },
    easytier: { available: Boolean(easytier), path: easytier },
  };
}

function status(deps = {}) {
  const adapters = privateAdapters(deps.networkInterfaces ? deps.networkInterfaces() : os.networkInterfaces());
  const clients = detectClients(deps.env || process.env);
  return {
    ok: true,
    schema: "hashmm.remote-network-bridge.v2",
    adapters,
    clients,
    overlayReady: adapters.length > 0,
    mode: adapters.length > 0 ? "external-overlay-ready" : "native-webrtc",
    routingPolicy: ["webrtc-direct", "turn-relay", "approved-overlay", "emergency-https-relay"],
    easyTier: {
      integration: "external-process-adapter",
      installed: Boolean(clients.easytier.available),
      active: adapters.some((item) => /easytier/i.test(item.name)),
      bundled: false,
      autoStart: false,
      secretCustody: "external-client",
      licenseBoundary: "LGPL-3.0-source-not-copied",
    },
    disclosure: "组网软件会优先尝试点对点直连；穿透失败时可能使用其配置的中继。HashMM 不读取或保存组网密钥。",
  };
}

function launch(executable, args, spawnImpl = spawn) {
  if (!executable) throw new Error("未找到所需客户端");
  const child = spawnImpl(executable, args, {
    detached: true,
    stdio: "ignore",
    windowsHide: false,
    shell: false,
  });
  if (child && typeof child.unref === "function") child.unref();
  return { ok: true, executable, args };
}

function launchRdp(targetValue, options = {}, deps = {}) {
  const target = normalizeTarget(targetValue);
  const port = normalizePort(options.port, 3389);
  const clients = detectClients(deps.env || process.env);
  if (!clients.rdp.available) throw new Error("未找到系统远程桌面客户端");
  const destination = `${target}:${port}`;
  const args = process.platform === "win32" ? [`/v:${destination}`] : [destination];
  return launch(clients.rdp.path, args, deps.spawn || spawn);
}

function launchMoonlight(targetValue, options = {}, deps = {}) {
  const target = normalizeTarget(targetValue);
  const clients = detectClients(deps.env || process.env);
  if (!clients.moonlight.available) throw new Error("未找到 Moonlight 客户端，请先从官方渠道安装并完成与 Sunshine 主机的配对");
  const appName = String(options.app || "Desktop").trim();
  if (!appName || appName.length > 80 || /[\r\n\0]/.test(appName)) throw new Error("串流应用名称无效");
  return launch(
    clients.moonlight.path,
    ["--display-mode", "windowed", "--absolute-mouse", "stream", target, appName],
    deps.spawn || spawn,
  );
}

module.exports = {
  normalizeTarget,
  normalizePort,
  privateAdapters,
  detectClients,
  status,
  launchRdp,
  launchMoonlight,
};
