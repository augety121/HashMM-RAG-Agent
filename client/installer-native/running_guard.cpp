// running_guard.cpp — InstallEngine 运行中守卫实现（V102）。
#include "running_guard.h"

#include <QProcess>
#include <QElapsedTimer>
#include <QThread>

namespace InstallEngine {

bool decideRunningFromTasklist(const QString& tasklistOutput, const QString& exeName) {
    if (exeName.isEmpty()) return false;
    // 大小写无关地判断输出里是否出现 exe 名。
    // tasklist 命中会在表格里打印 "HashMM.exe ... PID ..."; 未命中只打印
    // "信息: 没有运行的任务匹配指定标准。" / "INFO: No tasks ..."，不含 exe 名。
    return tasklistOutput.toLower().contains(exeName.toLower());
}

QStringList tasklistArgs(const QString& exeName) {
    // /NH 去表头，/FI 过滤镜像名，缩小输出、稳健匹配。
    return QStringList{
        QStringLiteral("/NH"),
        QStringLiteral("/FI"),
        QStringLiteral("IMAGENAME eq %1").arg(exeName),
    };
}

QStringList taskkillArgs(const QString& exeName, bool force) {
    QStringList a{ QStringLiteral("/IM"), exeName };
    if (force) {
        a << QStringLiteral("/F")   // 强制
          << QStringLiteral("/T");  // 连同子进程（内置 python/uvicorn/node 一锅端）
    }
    return a;
}

#ifdef Q_OS_WIN
bool isAppRunning(const QString& exeName) {
    QProcess p;
    p.start(QStringLiteral("tasklist"), tasklistArgs(exeName));
    if (!p.waitForStarted(3000)) return false;
    p.waitForFinished(5000);
    const QString out = QString::fromLocal8Bit(p.readAllStandardOutput());
    return decideRunningFromTasklist(out, exeName);
}

bool closeRunningApp(const QString& exeName, int graceMs, int timeoutMs) {
    if (!isAppRunning(exeName)) return true;

    QElapsedTimer clock;
    clock.start();

    // 1) 优雅关闭：请求主进程退出（不带 /F）。
    {
        QProcess k;
        k.start(QStringLiteral("taskkill"), taskkillArgs(exeName, /*force*/ false));
        k.waitForFinished(4000);
    }
    QThread::msleep(graceMs > 0 ? graceMs : 0);
    if (!isAppRunning(exeName)) return true;

    // 2) 还在 -> 强制 + 连子进程。可能要多敲几次，直到超时。
    while (clock.elapsed() < timeoutMs) {
        QProcess k;
        k.start(QStringLiteral("taskkill"), taskkillArgs(exeName, /*force*/ true));
        k.waitForFinished(4000);
        QThread::msleep(400);
        if (!isAppRunning(exeName)) return true;
    }
    // 3) 超时仍在：让调用方决定（提示用户手动关闭）。
    return !isAppRunning(exeName);
}
#else
// 非 Windows（你的开发/打包机若是 mac/linux）：不拦，直接放行。
bool isAppRunning(const QString&) { return false; }
bool closeRunningApp(const QString&, int, int) { return true; }
#endif

} // namespace InstallEngine
