# HashMM 原生安装器（V2802 源码）

正式桌面交付入口是 `build-all.bat`。它在 Windows 上生成一个可直接双击的
`HashMM-Setup.exe`，Qt 只用于安装界面，应用本体仍是 Electron。

## 一键出包

双击 `build-all.bat`，或在自动化环境中运行：

```bat
set HASHMM_NO_PAUSE=1
installer-native\build-all.bat
```

流水线会依次执行：

1. 编译 Qt6 原生安装器并部署所需 DLL。
2. 校验 npm 锁文件，运行前端测试、TypeScript 检查与生产构建。
3. 验证桌面依赖树、后端管理器与真实 Git 托管 worktree 测试。
4. 严格校验内置 Python 的 requirements 指纹与核心模块导入。
5. 构建 `win-unpacked`，检查前端、V2802 后端、app.asar 和运行时确实进入成品。
6. 用唯一临时目录自解压外壳打成单个 EXE。
7. 可选 Authenticode 签名，并生成 SHA-256 与发行清单。

成功产物：

- `HashMM-Setup.exe`：给用户安装的单文件。
- `HashMM-Setup.exe.sha256`：下载完整性校验。
- `HashMM-Setup.release.json`：桌面版本、后端迭代号、体积和 SHA-256。

## 当前安装保证

- 安装包内置完整 Python 3.12 运行时；干净电脑无需系统 Python、pip 或首次联网下载。
- 安装/升级先复制到同盘 staging，校验关键文件后再切换；失败恢复旧版本。
- 更新保留 `HashMM Files`、`HashMM Data` 和 `local-backend`，不把旧程序残留混进新版。
- 安装前按真实 payload 计算磁盘空间，不再显示硬编码体积。
- 自解压每次使用唯一 `%TEMP%` 目录，安装器退出后自动清理。
- 远程能力包必须使用 HTTPS 并绑定 SHA-256；loopback 仅保留给本机调试。
- `data/hooks/*.py` 不会因文件出现就执行；只有管理员明确绑定当前 SHA-256 后，才会在下次启动加载。
- Python Hook 文件变化或信任撤销会让已加载回调立即失效；声明式 block/confirm 规则走统一工具入口。
- 共享浏览器页面、窄 preload、站点权限策略和轨迹评测模块均进入 app.asar；安装后不是演示页或源码孤岛。
- 深度检索、共享浏览器、记忆/质量/技能/路由上下文和定时任务结果都接入 Chat 主链；功能上下文在服务端限量、脱敏并按不可信数据隔离。
- 普通 RAG 与 AgentLoop 回答都携带逐主张证据账本；多次检索使用整轮稳定引用编号，历史消息可重放同一份证据映射。
- 多 Agent 先共享一次真实知识库检索，再按角色并行分析；共享来源、角色轨迹、综合结果和证据账本随 Chat 消息持久化，重启后可恢复。
- 多 Agent 任务暴露 owner-safe 的真实停止、终态重跑与 retry lineage；停止是协作式语义，已发出的模型调用返回后丢弃，不伪报为硬杀。
- 浏览器只有成功 `read` 且带真实页面正文的事件才能成为来源；URL 凭据、fragment 与 secret-like 查询参数在桌面和后端双重删除，导航/点击只保留为执行轨迹。
- Chat 右侧证据栏可准备深度复核或受控浏览器核验，并切换到真实 Chat dispatch 模式；仍由用户确认发送，不自动触发外部访问。
- 桌面生产界面执行零 emoji 门禁；右侧上下文栏统一展示证据覆盖、多 Agent 状态、执行轨迹、文件和画布入口。

## 公网发布签名

公开分发应准备代码签名证书，并设置：

```bat
set HASHMM_SIGN_PFX=C:\secure\hashmm-code-signing.pfx
set HASHMM_SIGN_PASSWORD=通过安全环境注入，不要写进 bat
set HASHMM_REQUIRE_SIGNING=1
build-all.bat
```

没有证书时流水线仍可生成内部测试包，但会明确标为 unsigned；当
`HASHMM_REQUIRE_SIGNING=1` 时，无签名或验签失败会直接阻止出包。

## 主要源码

- `InstallerWindow.cpp` / `install_engine.cpp`：安装 UI、事务更新与回滚。
- `bootstrap/bootstrap.c`：纯 Win32 唯一临时目录自解压外壳。
- `bootstrap/pack.py`：确定性、流式 ZIP 打包，拒绝 symlink payload。
- `../desktop/scripts/verify-release.py`：源码、运行时、成品与 Artifact 门禁。
