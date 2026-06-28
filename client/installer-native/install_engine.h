#pragma once
// install_engine.h — HashMM 原生安装器的安装逻辑（对照已测 install-engine.js）。
#include <QString>
#include <functional>

namespace InstallEngine {

// 产品名 / 标记文件名（与 install-engine.js 一致）
QString product();
QString markerFileName();

// 默认安装目录：%LOCALAPPDATA%\Programs\HashMM
QString defaultInstallDir();

// 校验安装目录是否安全可写。不安全则把原因写入 reason，返回 false。
// 拒绝：空 / 非盘符绝对路径 / 盘符根 / 系统关键目录 / Windows 目录树。
bool validateInstallDir(const QString& dir, QString* reason);

// 归一化安装目录（去多余分隔符、统一反斜杠、去尾分隔符）。
QString normalizeInstallDir(const QString& dir);

// 已安装 exe 名（始终 HashMM.exe——app 主二进制名）。
QString installedExeName();

// 安装标记文件完整路径：<installDir>\<markerFileName>
QString markerPath(const QString& installDir);

// 写安装标记（JSON：product/version/installedAt）。成功返回 true。
bool writeMarker(const QString& installDir, const QString& version);

// 上次安装记录文件路径（写在 %APPDATA%\HashMM，供再次运行检测已安装）。
QString lastInstallRecordPath();
bool writeLastInstallRecord(const QString& installDir, const QString& version);
// 读上次安装记录；若记录的目录仍有标记 → 返回该目录，否则空串。
QString readValidLastInstall();

// 递归拷贝目录树（payload → 安装目录）。onProgress(pct 0..100) 可空。
// 排除运行期/无关项（data、logs、.cache、标记文件）。
bool copyTree(const QString& from, const QString& to,
              std::function<void(int)> onProgress, QString* err);

// 估算源目录文件总数（给进度用）。
int countFiles(const QString& dir);

// 生成创建快捷方式的 PowerShell 命令（与 install-engine.js shortcutPsCommand 同形）。
// args 非空时写入 $s.Arguments（卸载快捷方式用 "--uninstall"）。
QString shortcutPsCommand(const QString& lnkPath, const QString& targetPath,
                          const QString& workingDir, const QString& iconPath,
                          const QString& description, const QString& args = QString());

// 写控制面板卸载项（HKCU\...\Uninstall\HashMM）。UninstallString = "<exe>" --uninstall。
void writeUninstallRegistry(const QString& installDir, const QString& version,
                            const QString& uninstallExe);

// 开始菜单 / 桌面 / 卸载快捷方式的目标路径
QString startMenuDir();
QString desktopDir();

// ── 卸载 ──
// 卸载注册表键（HKCU 下相对路径）。
QString uninstallRegistryKey();
// 卸载要删的快捷方式完整路径（开始菜单 HashMM/卸载 HashMM + 桌面 HashMM）。
QStringList uninstallShortcutPaths();
// 自删 .bat：等 exe 退出 → 删注册表/快捷方式/安装目录（保留 preserveDirs 数据夹）→ 删自身。
// 对照已测 install-engine.js selfDeleteScript；preserveDirs 用链式 if 跳过（如 HashMM Files、local-backend）。
QString uninstallSelfDeleteScript(const QString& installDir, const QString& exeName,
                                  const QStringList& preserveDirs);

} // namespace InstallEngine
