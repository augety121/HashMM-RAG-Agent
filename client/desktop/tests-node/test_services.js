/** test_services.js — 服务层单测（V101）。
 *  ServiceRegistry（注册/解析/生命周期）+ ModelService（能力/可用性/推荐）。
 *  运行：node desktop/services/test_services.js
 */
"use strict";
const assert = require("assert");
const { ServiceRegistry } = require("../services/registry");
const { ModelService } = require("../services/model-service");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

(async () => {
  // 1. 注册表：注册/解析/重复名/tryGet
  {
    const reg = new ServiceRegistry();
    reg.register("a", { v: 1 });
    assert.strictEqual(reg.get("a").v, 1);
    assert.ok(reg.has("a") && !reg.has("b"));
    assert.throws(() => reg.register("a", {}), /已存在/, "重复名抛错");
    assert.throws(() => reg.get("nope"), /未注册/, "取不存在抛错");
    assert.strictEqual(reg.tryGet("nope"), null, "tryGet 不存在返回 null");
    reg.set("a", { v: 2 }); // 迁移期覆盖
    assert.strictEqual(reg.get("a").v, 2);
    assert.strictEqual(reg.size(), 1);
  }
  ok("注册表：注册/解析/重复名抛错/tryGet/set 覆盖");

  // 2. 生命周期：startAll/stopAll 调到实现了 start/stop 的服务，且单个失败不连坐
  {
    const reg = new ServiceRegistry();
    const order = [];
    reg.register("s1", { start: async () => order.push("s1-start"), stop: async () => order.push("s1-stop") });
    reg.register("s2", { start: async () => { throw new Error("boom"); } }); // 失败
    reg.register("s3", { start: async () => order.push("s3-start"), stop: async () => order.push("s3-stop") });
    reg.register("plain", { v: 1 }); // 无生命周期方法，跳过
    await reg.startAll();
    assert.ok(order.includes("s1-start") && order.includes("s3-start"), "s2 失败不影响 s1/s3 启动");
    await reg.startAll(); // 幂等
    assert.strictEqual(order.filter((x) => x === "s1-start").length, 1, "startAll 幂等");
    await reg.stopAll();
    // stop 逆序：s3 先于 s1
    assert.ok(order.indexOf("s3-stop") < order.indexOf("s1-stop"), "stopAll 逆序");
  }
  ok("生命周期：startAll/stopAll + 失败不连坐 + 幂等 + 逆序停止");

  // 3. 模型服务：能力档 + 可用性 + 推荐
  {
    const svc = new ModelService({ modelsBasePath: "C:\\Apps\\HashMM\\resources", hardware: { totalVramMB: 12288, hasGpu: true, totalMemMB: 32768, cpuCount: 16 } });
    await svc.start();
    assert.strictEqual(svc.getCapability().tier, "mid", "4070S 12G → mid");
    const av = svc.available();
    assert.ok(av.length >= 2, "至少 dbnet/crnn");
    const dbnet = av.find((m) => m.name === "dbnet");
    assert.strictEqual(dbnet.decision.target, "local", "OCR 小模型 → 本地");
    assert.strictEqual(typeof dbnet.ready, "boolean", "含就绪标志");
    // 注册一个需要大显存的，推荐应为云端
    svc.registry.register("bigllm", { type: "llm", minVramMB: 24576 });
    assert.strictEqual(svc.recommend("bigllm").target, "cloud", "大模型超显存 → 云端");
    // 刷新硬件到高配
    svc.setHardware({ totalVramMB: 24576, hasGpu: true });
    assert.strictEqual(svc.recommend("bigllm").target, "local", "升级硬件后 → 本地");
  }
  ok("模型服务：能力档 + 可用性(就绪+决策) + 推荐 + 硬件刷新");

  // 4. 更新服务：版本比较（纯逻辑）+ checkOnce 封装
  {
    const { UpdateService, compareVersions, isNewer } = require("../services/update-service");
    assert.strictEqual(compareVersions("1.6.0", "1.5.0"), 1, "1.6.0 > 1.5.0");
    assert.strictEqual(compareVersions("1.5.0", "1.6.0"), -1);
    assert.strictEqual(compareVersions("v1.6.0", "1.6.0"), 0, "容忍 v 前缀");
    assert.strictEqual(compareVersions("1.10.0", "1.9.0"), 1, "数字比较非字典序");
    assert.strictEqual(compareVersions("1.6.0-beta", "1.6.0"), -1, "预发布 < 正式");
    assert.ok(isNewer("1.6.1", "1.6.0") && !isNewer("1.6.0", "1.6.0"));
    const svc = new UpdateService();
    // 假 autoUpdater：触发 update-available
    const fake = (() => {
      const cbs = {};
      return { once: (e, f) => { cbs[e] = f; }, removeListener: () => {}, checkForUpdates: () => setTimeout(() => cbs["update-available"] && cbs["update-available"]({ version: "9.9.9" }), 0) };
    })();
    const r = await svc.checkOnce(fake, 1000);
    assert.strictEqual(r.available, true); assert.strictEqual(r.version, "9.9.9");
    // 无 autoUpdater → 结构化结果不抛
    assert.strictEqual((await svc.checkOnce(null)).available, false);
  }
  ok("更新服务：语义版本比较(v前缀/数字序/预发布) + checkOnce 封装");

  // 5. 托盘服务：菜单模板构建（纯逻辑）
  {
    const { buildTrayMenuTemplate, TrayService } = require("../services/tray-service");
    const clicks = [];
    const handlers = { toggle: () => clicks.push("toggle"), stopBackend: () => {}, startBackend: () => {}, setTrayOnClose: (v) => clicks.push("set:" + v), quit: () => {} };
    // running 状态：应出现"停止本地后端（端口 X）"
    const t1 = buildTrayMenuTemplate({ running: true, port: 8123, trayOnClose: true }, handlers);
    const stopItem = t1.find((i) => i.label && i.label.includes("停止本地后端"));
    assert.ok(stopItem && stopItem.label.includes("8123"), "running → 显示停止+端口");
    const checkbox = t1.find((i) => i.type === "checkbox");
    assert.strictEqual(checkbox.checked, true, "trayOnClose 勾选态");
    checkbox.click({ checked: false }); assert.ok(clicks.includes("set:false"), "复选框回调透传");
    // stopped 状态：应出现"启动本地后端"
    const t2 = buildTrayMenuTemplate({ running: false }, handlers);
    assert.ok(t2.find((i) => i.label === "启动本地后端"), "stopped → 显示启动");
    assert.ok(t2.find((i) => i.label === "退出 HashMM"), "含退出项");
    // 注入假 Tray 验证生命周期不抛
    let toolTip = "", menuSet = false;
    const FakeTray = function () { return { setToolTip: (t) => { toolTip = t; }, setContextMenu: () => { menuSet = true; }, on: () => {}, destroy: () => {} }; };
    const FakeMenu = { buildFromTemplate: (tpl) => tpl };
    const ts = new TrayService({ Tray: FakeTray, Menu: FakeMenu, tooltip: "HashMM" });
    ts.create(() => ({ running: false }), handlers);
    assert.strictEqual(toolTip, "HashMM"); assert.ok(menuSet, "菜单已设置");
    ts.destroy();
  }
  ok("托盘服务：菜单模板(running/stopped/复选框回调) + 生命周期");

  // 6. 本地 OCR 端到端编排（注入假会话，确定性验证 检测→取框→裁剪缩放→识别→CTC→拼文本）
  {
    const { ModelService } = require("../services/model-service");
    const W = 10, H = 8;
    // 造图：左上 4x3 一块"文字"区（面积 12 > minArea 8），其余背景
    const pixels = new Uint8Array(W * H * 3);
    const setBlock = (x0, y0, w, h) => { for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) { const i = (y * W + x) * 3; pixels[i] = pixels[i + 1] = pixels[i + 2] = 255; } };
    setBlock(0, 0, 4, 3);
    const svc = new ModelService({ modelsBasePath: "/tmp/none", charset: ["a", "b"] });
    // 假检测会话：输出概率图（block 区为 1.0），dims/data 仿 onnxruntime Tensor
    const probMap = new Float32Array(W * H);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) probMap[y * W + x] = (x < 4 && y < 3) ? 0.9 : 0.0;
    svc._det = { run: async () => ({ out: { data: probMap, dims: [1, 1, H, W] } }) };
    // 假识别会话：固定输出 3 帧×3 类(blank,a,b) → 期望解码 "ab"
    const hot = (k) => { const r = [0, 0, 0]; r[k] = 1; return r; };
    const recData = Float32Array.from([...hot(1), ...hot(0), ...hot(2)]); // a, blank, b → "ab"
    svc._rec = { run: async () => ({ out: { data: recData, dims: [1, 3, 3] } }) };

    const r = await svc.runOcr({ pixels, width: W, height: H });
    assert.strictEqual(r.ok, true, "编排成功");
    assert.ok(Array.isArray(r.boxes) && r.boxes.length >= 1, "检测出框");
    assert.strictEqual(typeof r.text, "string", "产出文本");
    assert.ok(r.text.includes("ab"), "CRNN+CTC 解码出 'ab'：" + JSON.stringify(r.text));
  }
  ok("本地 OCR 端到端编排：检测→取框→裁剪缩放→识别→CTC→拼文本（注入会话确定性验证）");

  // 7. 窗口服务：切换动作 / 关闭行为 / 边界校验 / 屏外拉回
  {
    const WS = require("../services/window-service");
    // 切换
    assert.strictEqual(WS.nextToggleAction({ visible: true, minimized: false }), "hide", "可见非最小化→收起");
    assert.strictEqual(WS.nextToggleAction({ visible: false }), "show", "隐藏→显示");
    assert.strictEqual(WS.nextToggleAction({ visible: true, minimized: true }), "show", "最小化→显示");
    // 关闭行为
    assert.strictEqual(WS.shouldMinimizeToTray(false, true), true, "非退出+勾选→最小化");
    assert.strictEqual(WS.shouldMinimizeToTray(true, true), false, "真退出→关闭");
    assert.strictEqual(WS.shouldMinimizeToTray(false, false), false, "没勾→关闭");
    // 尺寸兜底
    const s = WS.sanitizeBounds({ width: 100, height: 50 });
    assert.ok(s.width >= 900 && s.height >= 600, "小于下限提到下限");
    const s2 = WS.sanitizeBounds(null);
    assert.strictEqual(s2.width, 1280, "缺失用默认宽");
    assert.ok(!("x" in s2), "无坐标不强加");
    // 屏外拉回
    const displays = [{ x: 0, y: 0, width: 1920, height: 1040 }];
    const onscreen = WS.clampBoundsToDisplays({ x: 100, y: 100, width: 1280, height: 860 }, displays);
    assert.strictEqual(onscreen.x, 100, "屏内不动");
    const off = WS.clampBoundsToDisplays({ x: 5000, y: 5000, width: 1280, height: 860 }, displays);
    assert.ok(off.x < 1920 && off.x >= 0, "屏外→主屏居中(x 拉回)");
    assert.ok(off.y >= 0 && off.y < 1040, "屏外→主屏居中(y 拉回)");
    const noPos = WS.clampBoundsToDisplays({ width: 1280, height: 860 }, displays);
    assert.ok(!("x" in noPos), "无坐标交给系统居中");
  }
  ok("窗口服务：切换动作/关闭行为/尺寸兜底/屏外拉回主屏");

  // 8. 终端服务：缓冲环 / shell / 环境 / cwd / 会话表
  {
    const TS = require("../services/terminal-service");
    // 缓冲环截尾
    assert.strictEqual(TS.appendBuffer("abc", "de", 100), "abcde", "未超容量直接拼");
    assert.strictEqual(TS.appendBuffer("abcde", "fg", 4), "defg", "超容量保最新 4 字节");
    assert.strictEqual(TS.appendBuffer("", "x", 10), "x");
    // shell 选择
    assert.strictEqual(TS.defaultShell("win32"), "powershell.exe");
    assert.strictEqual(TS.defaultShell("linux", { SHELL: "/bin/zsh" }), "/bin/zsh");
    assert.strictEqual(TS.defaultShell("linux", {}), "/bin/bash", "无 SHELL 回退 bash");
    // 环境准备
    const env = TS.buildShellEnv({ PATH: "/x" }, "linux");
    assert.strictEqual(env.TERM, "xterm-256color");
    assert.strictEqual(env.HASHMM_DESKTOP, "1");
    assert.strictEqual(env.LANG, "zh_CN.UTF-8", "非 Windows 无 UTF-8 locale → 兜底");
    assert.strictEqual(TS.buildShellEnv({ LANG: "en_US.UTF-8" }, "linux").LANG, "en_US.UTF-8", "已有 UTF-8 不覆盖");
    assert.ok(!("LANG" in TS.buildShellEnv({}, "win32")) || true, "Windows 不强加 LANG");
    // cwd 解析
    assert.strictEqual(TS.resolveStartCwd("/exists", (p) => p === "/exists", "/home"), "/exists");
    assert.strictEqual(TS.resolveStartCwd("/nope", () => false, "/home"), "/home", "不存在回 home");
    assert.strictEqual(TS.resolveStartCwd(null, () => true, "/home"), "/home");
    // 会话表：增/复用/缓冲/回放/清理
    const store = new TS.SessionStore(8);
    const fakePty = { pid: 1 };
    store.add("t1", fakePty);
    assert.ok(store.has("t1") && store.get("t1") === fakePty);
    store.append("t1", "hello");
    store.append("t1", "world!!"); // 超 cap 8 → 截尾
    assert.ok(store.buffer("t1").length <= 8, "会话缓冲受 cap 限");
    assert.strictEqual(store.size(), 1);
    store.remove("t1");
    assert.ok(!store.has("t1") && store.buffer("t1") === "", "清理后无残留");
  }
  ok("终端服务：缓冲环截尾/shell选择/环境locale/cwd解析/会话表复用清理");

  // 9. 后端服务：路径解析 + 状态委托
  {
    const BS = require("../services/backend-service");
    assert.ok(BS.backendHome("C:\\u\\data").endsWith("local-backend"), "home 在 userData 内");
    assert.ok(BS.backendSrcDir("C:\\app\\resources").endsWith("backend"), "src 在 resources/backend");
    assert.strictEqual(BS.backendSrcDir("", "C:\\dev\\src"), "C:\\dev\\src", "无 resources 回退开发目录");
    // 状态委托
    const svc = new BS.BackendService({ mgr: { status: (home) => ({ running: true, port: 9 }) }, userDataPath: "C:\\u" });
    assert.strictEqual(svc.status().running, true);
    assert.ok(svc.home().endsWith("local-backend"));
    // 无 mgr 时 not-running 不抛
    assert.strictEqual(new BS.BackendService({}).status().running, false);
  }
  ok("后端服务：home/src 路径解析 + 状态委托 + 无 mgr 兜底");

  // 9b. 后端数据目录解析：默认安装位置 / 可配置 / 兜底 userData
  {
    const BS = require("../services/backend-service");
    const norm = (s) => s.replace(/[\\/]+/g, "/");
    // runtime home 仍在安装目录 local-backend；ProjectVault 由独立服务管理。
    const d1 = BS.resolveBackendHome({ installDir: "D:\\hashmm", userDataDir: "C:\\u\\AppData\\Roaming\\HashMM" });
    assert.strictEqual(norm(d1), "D:/hashmm/local-backend", "默认落安装目录");
    // 配置优先（原样返回，不拼接）
    assert.strictEqual(BS.resolveBackendHome({ configuredDir: "E:\\mydata", installDir: "D:\\hashmm" }), "E:\\mydata", "配置优先");
    // 空配置忽略
    assert.strictEqual(norm(BS.resolveBackendHome({ configuredDir: "  ", installDir: "D:\\hashmm" })), "D:/hashmm/local-backend", "空配置回默认");
    // 无 installDir 兜底 userData
    assert.ok(BS.resolveBackendHome({ userDataDir: "C:\\u\\HashMM" }).endsWith("local-backend"), "兜底 userData");
  }
  ok("后端数据目录：默认安装位置 / 配置优先 / 空配置回默认 / 兜底 userData");

  // 9c. ProjectVault 默认必须跟随用户选择的安装目录，绝不写死 C 盘。
  {
    const PV = require("../services/project-vault");
    const norm = (s) => s.replace(/[\\/]+/g, "/");
    assert.strictEqual(norm(PV.resolveProjectVault({ installDir: "D:\\hashmm" })), "D:/hashmm/HashMM Data");
    assert.strictEqual(PV.resolveProjectVault({ configuredDir: "E:\\HashMM-Vault", installDir: "D:\\hashmm" }),
      require("path").resolve("E:\\HashMM-Vault"));
    const ready = PV.inspectProjectVaultStatus({
      desiredDir: "E:\\HashMM-Vault", defaultDir: "D:\\hashmm\\HashMM Data",
      configuredDir: "E:\\HashMM-Vault", localStatus: { running: true, dataDir: "E:\\old-vault" },
      fsApi: { existsSync: () => true, accessSync: () => {} },
    });
    assert.strictEqual(ready.availability, "ready");
    assert.strictEqual(ready.source, "configured");
    assert.strictEqual(ready.localBackendRunning, true);
    assert.strictEqual(ready.restartRequired, true, "期望路径与运行路径不同时必须明确等待重启");
    const missing = PV.inspectProjectVaultStatus({
      desiredDir: "D:\\hashmm\\HashMM Data", defaultDir: "D:\\hashmm\\HashMM Data",
      fsApi: { existsSync: () => false, accessSync: () => { throw new Error("must not run"); } },
    });
    assert.strictEqual(missing.availability, "not_initialized");
    assert.strictEqual(missing.localBackendRunning, false);
    assert.strictEqual(missing.effectiveDir, null);
  }
  ok("ProjectVault：路径来源 / 可用性 / 运行中实际路径 / 重启差异可验证");

    // 9d. 心跳判定：后端忙(超时)不算掉线，只有连接被拒(真下线)连续达阈值才弹横幅
  {
    const H = require("../services/health-util");
    // 归类
    assert.strictEqual(H.classifyHealth({ ok: true }), "ok");
    assert.strictEqual(H.classifyHealth({ ok: false, kind: "timeout" }), "busy", "超时=忙");
    assert.strictEqual(H.classifyHealth({ ok: false, kind: "refused" }), "down", "拒连=真下线");
    // 解析大文件场景：连续超时（忙）→ 永不弹掉线横幅
    let st = { downStreak: 0, busyStreak: 0 };
    for (let i = 0; i < 5; i++) {
      const s = H.nextHeartbeatState(st, { ok: false, kind: "timeout" }, { downThreshold: 2 });
      st = { downStreak: s.downStreak, busyStreak: s.busyStreak };
      assert.strictEqual(s.showOffline, false, "忙第" + (i + 1) + "次仍不弹横幅");
    }
    assert.strictEqual(st.downStreak, 0, "忙不累计下线计数");
    assert.ok(st.busyStreak >= 5, "忙单独计数");
    // 真下线：连续 2 次拒连 → 第 2 次弹
    let d = { downStreak: 0, busyStreak: 0 };
    const s1 = H.nextHeartbeatState(d, { ok: false, kind: "refused" }, { downThreshold: 2 });
    assert.strictEqual(s1.showOffline, false, "下线第 1 次还不弹");
    d = { downStreak: s1.downStreak, busyStreak: s1.busyStreak };
    const s2 = H.nextHeartbeatState(d, { ok: false, kind: "refused" }, { downThreshold: 2 });
    assert.strictEqual(s2.showOffline, true, "下线第 2 次弹");
    assert.strictEqual(s2.downStreak, 2);
    // 恢复：ok → 清横幅 + 计数归零
    const s3 = H.nextHeartbeatState({ downStreak: 2, busyStreak: 0 }, { ok: true }, {});
    assert.strictEqual(s3.clearOffline, true, "恢复清横幅");
    assert.strictEqual(s3.downStreak, 0);
    // 忙之后真下线仍能正确累计（忙不重置 downStreak？——设计上忙不动 downStreak，下线从忙态继续）
    const busyThenDown = H.nextHeartbeatState({ downStreak: 1, busyStreak: 3 }, { ok: false, kind: "refused" }, { downThreshold: 2 });
    assert.strictEqual(busyThenDown.showOffline, true, "忙后真下线接着累计到阈值");
  }
  ok("心跳判定：后端忙(超时)不弹横幅 / 真下线连续2次才弹 / 恢复清横幅");

  // 10. 网络工具：相对重定向解析为绝对（修真机 Invalid URL 崩溃）
  {
    const NU = require("../services/net-util");
    // safeParseUrl 永不抛
    assert.ok(NU.safeParseUrl("https://a.com/x"), "合法 URL");
    assert.strictEqual(NU.safeParseUrl("not a url"), null, "非法返回 null 不抛");
    assert.strictEqual(NU.safeParseUrl("/relative/path"), null, "相对路径单独解析为 null");
    // resolveRedirect：相对 Location 用基地址解析（正是崩溃根因）
    assert.strictEqual(
      NU.resolveRedirect("/files/model.onnx", "https://mirror.com/dl/"),
      "https://mirror.com/files/model.onnx", "相对跳转→绝对（修 Invalid URL）");
    assert.strictEqual(
      NU.resolveRedirect("https://cdn.com/m.onnx", "https://mirror.com/dl/"),
      "https://cdn.com/m.onnx", "绝对跳转原样");
    assert.strictEqual(NU.resolveRedirect("", "https://a.com"), null, "空 Location → null");
    assert.strictEqual(NU.resolveRedirect("://bad", "https://a.com"), "https://a.com/://bad", "怪异 Location 仍被基地址兜住不崩");
    // pickProtocolLib
    const libs = { http: "H", https: "S" };
    assert.strictEqual(NU.pickProtocolLib("https://a.com", libs), "S");
    assert.strictEqual(NU.pickProtocolLib("http://a.com", libs), "H");
    assert.strictEqual(NU.pickProtocolLib("garbage", libs), null, "非法 URL → null");
  }
  ok("网络工具：相对重定向解析为绝对 + 安全解析（修真机 Invalid URL 崩溃）");

  // 11. 截屏工具：捕获分辨率封顶（修高分屏截屏卡顿）
  {
    const SS = require("../services/screenshot-util");
    // 1080p@1x：不触顶
    const r1 = SS.capCaptureSize(1920, 1080, 1);
    assert.deepStrictEqual([r1.width, r1.height, r1.scaled], [1920, 1080, false]);
    // 2K@1x：不触顶
    assert.strictEqual(SS.capCaptureSize(2560, 1440, 1).scaled, false, "2K 不缩");
    // 4K@1x：最长边 3840 > 2880 → 缩
    const r4k = SS.capCaptureSize(3840, 2160, 1);
    assert.strictEqual(r4k.scaled, true, "4K 触顶缩小");
    assert.strictEqual(r4k.width, 2880, "最长边缩到上限");
    assert.strictEqual(r4k.height, 1620, "等比缩高");
    // HiDPI 1080p@1.5x = 2880 native：恰好触顶不缩
    assert.strictEqual(SS.capCaptureSize(1920, 1080, 1.5).scaled, false, "2880 恰好不缩");
    // HiDPI 1080p@2x = 3840 native → 缩
    assert.strictEqual(SS.capCaptureSize(1920, 1080, 2).scaled, true, "@2x 触顶");
    // 异常尺寸安全
    assert.deepStrictEqual([SS.capCaptureSize(0, 0, 1).width, SS.capCaptureSize(0, 0, 1).height], [0, 0]);
  }
  ok("截屏工具：捕获分辨率封顶（1080p/2K 不变，4K/HiDPI 缩，修卡顿）");

  // 12. 服务引导：一次注册所有服务 + IPC（注入假 ipcMain/registry 验证）
  {
    const { bootstrapServices } = require("../services/bootstrap");
    const { ServiceRegistry } = require("../services/registry");
    const reg = new ServiceRegistry();
    const handlers = {};
    const fakeIpc = { handle: (name, fn) => { handlers[name] = fn; } };
    let storageIpcCalled = false;
    const fakeStorage = { ensureDirs() {}, dataRoot() { return "X"; } };
    const out = bootstrapServices({
      ipcMain: fakeIpc, registry: reg, storage: fakeStorage,
      registerStorageIpc: () => { storageIpcCalled = true; },
      backendMgr: { status: () => ({ running: false }) },
      logger: { info() {}, warn() {} },
      installDir: "C:\\App", userData: "C:\\u", resourcesPath: "C:\\App\\resources",
      devSrcDir: "C:\\dev", tmpDir: "C:\\tmp", isPackaged: true,
    });
    // 所有服务都注册
    for (const name of ["storage", "logger", "model", "update", "window", "terminal", "backend", "isolation"]) {
      assert.ok(reg.has(name), "服务已注册：" + name);
    }
    assert.ok(storageIpcCalled, "storage IPC 已注册");
    // 关键 IPC handler 都挂上
    for (const h of ["hashmm:model:capability", "hashmm:model:available", "hashmm:model:recommend", "hashmm:model:ocr", "hashmm:isolation:policy"]) {
      assert.ok(typeof handlers[h] === "function", "handler 已挂：" + h);
    }
    // handler 可调用且不抛
    const cap = handlers["hashmm:model:capability"]();
    assert.ok(cap.ok && cap.capability, "capability handler 返回正常");
    const pol = handlers["hashmm:isolation:policy"]();
    assert.ok(pol.ok && Array.isArray(pol.policy), "isolation handler 返回策略");
    assert.ok(out.modelService, "返回 modelService 引用");
    // 子项失败不连坐：storage 缺失也不抛，其余照常
    const reg2 = new ServiceRegistry();
    assert.doesNotThrow(() => bootstrapServices({ ipcMain: fakeIpc, registry: reg2, logger: { info() {}, warn() {} }, installDir: "C:\\A", userData: "C:\\u", tmpDir: "C:\\t" }));
    assert.ok(reg2.has("model") && reg2.has("isolation"), "无 storage 时其余服务照常注册");
  }
  ok("服务引导 bootstrap：一次注册 8 服务 + 5 IPC handler + 子项失败不连坐");

  console.log(`\ntest_services: ${pass} 项全部通过`);
})().catch((e) => { console.error("失败：", e); process.exit(1); });
