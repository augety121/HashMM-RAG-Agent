#pragma once
// running_guard.h — 「软件运行中 -> 关闭再装/卸」守卫（V102，对标微信安装体验）。
//
// 为什么需要：Windows 会锁住正在运行的 HashMM.exe 及其 DLL，覆盖/删除都会失败。
// 所以装到已存在目录、或卸载前，必须先确保旧进程退出 —— 这正是微信那样做的根因。
//
// 这是 InstallEngine 命名空间的**增量**文件：把它和 running_guard.cpp 一起加进
// CMakeLists 的 add_executable 列表即可，不用改你已测过的 install_engine.*。
//
// 纯逻辑（decideRunningFromTasklist / 命令构造）做成可单测的自由函数；真正的
// 进程查询/终止走 QProcess（tasklist / taskkill），Windows 上同步执行。
#include <QString>
#include <QStringList>

namespace InstallEngine {

// 纯函数：给定 tasklist 输出与目标 exe 名，判断进程是否在跑。
// 规则：输出（不区分大小写）是否包含 "<exe>"。tasklist 无匹配时打印
// "信息: 没有运行的任务匹配指定标准。"/"INFO: No tasks..."，都不含 exe 名 -> false。
// 可单测（见 running_guard 的 g++ 镜像测试）。
bool decideRunningFromTasklist(const QString& tasklistOutput, const QString& exeName);

// 纯函数：构造 tasklist 查询参数（QProcess 用）。默认查 HashMM.exe。
QStringList tasklistArgs(const QString& exeName);

// 纯函数：构造 taskkill 参数。force=false 走优雅（仅请求关闭主进程）；
// force=true 走强制 + 连子进程（/F /T，把内置 python/uvicorn/node 一起收）。
QStringList taskkillArgs(const QString& exeName, bool force);

// 实跑：目标 exe 是否正在运行（QProcess 调 tasklist /NH /FI）。Windows 专用；
// 非 Windows 一律返回 false（开发机不拦）。
bool isAppRunning(const QString& exeName);

// 实跑：先优雅关，等 graceMs，仍在跑则强制关（/F /T），最后再确认一次。
// 返回 true 表示已不在运行（可以安全装/卸）。timeoutMs 为总预算。
bool closeRunningApp(const QString& exeName, int graceMs = 1500, int timeoutMs = 8000);

} // namespace InstallEngine
