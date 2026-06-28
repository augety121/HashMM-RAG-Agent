// install_engine.cpp — 实现，逐项对照 install-engine.js（已在 Node 侧单测过的逻辑）。
#include "install_engine.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QDirIterator>
#include <QStandardPaths>
#include <QSettings>
#include <QJsonObject>
#include <QJsonDocument>
#include <QDateTime>
#include <QRegularExpression>

namespace InstallEngine {

QString product() { return QStringLiteral("HashMM"); }
QString markerFileName() { return QStringLiteral(".hashmm-install.json"); }

QString defaultInstallDir() {
    QString localAppData = qEnvironmentVariable("LOCALAPPDATA");
    if (localAppData.isEmpty())
        localAppData = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    return QDir::cleanPath(localAppData + "/Programs/" + product()).replace('/', '\\');
}

QString normalizeInstallDir(const QString& dir) {
    QString d = dir.trimmed();
    d.replace(QRegularExpression("[\\\\/]+"), "\\");
    while (d.endsWith('\\')) d.chop(1);
    return d;
}

bool validateInstallDir(const QString& dir, QString* reason) {
    auto fail = [&](const QString& r) { if (reason) *reason = r; return false; };
    QString d = dir.trimmed();
    if (d.isEmpty()) return fail(QStringLiteral("安装路径为空"));
    if (!QRegularExpression("^[a-zA-Z]:[\\\\/]").match(d).hasMatch())
        return fail(QStringLiteral("请使用带盘符的绝对路径（如 C:\\…）"));
    QString low = d.toLower();
    low.replace(QRegularExpression("[\\\\/]+$"), "");
    if (QRegularExpression("^[a-z]:$").match(low).hasMatch())
        return fail(QStringLiteral("不能直接装在盘符根目录"));
    static const QStringList sysDirs = {
        "c:\\windows", "c:\\program files", "c:\\program files (x86)",
        "c:\\", "c:\\users", "c:\\programdata"
    };
    for (const auto& s : sysDirs) if (low == s) return fail(QStringLiteral("不能装在系统目录"));
    if (low.startsWith("c:\\windows\\")) return fail(QStringLiteral("不能装在 Windows 目录内"));
    if (reason) reason->clear();
    return true;
}

QString installedExeName() { return QStringLiteral("HashMM.exe"); }

QString markerPath(const QString& installDir) {
    return QDir::cleanPath(installDir + "/" + markerFileName()).replace('/', '\\');
}

bool writeMarker(const QString& installDir, const QString& version) {
    QJsonObject o;
    o["product"] = product();
    o["version"] = version;
    o["installedAt"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    QFile f(markerPath(installDir));
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) return false;
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    f.close();
    return true;
}

QString lastInstallRecordPath() {
    QString appData = qEnvironmentVariable("APPDATA");
    if (appData.isEmpty())
        appData = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QString dir = QDir::cleanPath(appData + "/" + product());
    QDir().mkpath(dir);
    return QDir::cleanPath(dir + "/.hashmm-last-install.json").replace('/', '\\');
}

bool writeLastInstallRecord(const QString& installDir, const QString& version) {
    QJsonObject o;
    o["installDir"] = installDir;
    o["version"] = version;
    o["exeName"] = installedExeName();
    o["at"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    QFile f(lastInstallRecordPath());
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) return false;
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    f.close();
    return true;
}

QString readValidLastInstall() {
    QFile f(lastInstallRecordPath());
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return QString();
    auto doc = QJsonDocument::fromJson(f.readAll()); f.close();
    if (!doc.isObject()) return QString();
    QString dir = doc.object().value("installDir").toString();
    if (!dir.isEmpty() && QFileInfo::exists(markerPath(dir))) return dir;
    return QString();
}

int countFiles(const QString& dir) {
    int n = 0;
    QDirIterator it(dir, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) { it.next(); ++n; }
    return n;
}

bool copyTree(const QString& from, const QString& to,
              std::function<void(int)> onProgress, QString* err) {
    static const QStringList exclude = { "data", "logs", ".cache", ".hashmm-install.json" };
    QDir().mkpath(to);
    int total = countFiles(from); if (total <= 0) total = 1;
    int done = 0;
    QDirIterator it(from, QDir::AllEntries | QDir::NoDotAndDotDot, QDirIterator::Subdirectories);
    // 先建目录结构 + 拷文件（QDirIterator 已递归）
    while (it.hasNext()) {
        QString src = it.next();
        QFileInfo fi(src);
        QString rel = QDir(from).relativeFilePath(src);
        // 排除顶层项
        QString topSeg = rel.section(QRegularExpression("[\\\\/]"), 0, 0);
        if (exclude.contains(topSeg)) continue;
        QString dst = QDir::cleanPath(to + "/" + rel);
        if (fi.isDir()) {
            if (!QDir().mkpath(dst)) { if (err) *err = "建目录失败: " + dst; return false; }
        } else {
            QDir().mkpath(QFileInfo(dst).absolutePath());
            QFile::remove(dst); // 幂等：覆盖
            if (!QFile::copy(src, dst)) { if (err) *err = "拷文件失败: " + src; return false; }
            if (onProgress && (++done % 8 == 0)) onProgress(qMin(99, done * 100 / total));
        }
    }
    if (onProgress) onProgress(100);
    return true;
}

static QString psQuote(const QString& s) {
    QString t = s; t.replace("'", "''");
    return "'" + t + "'";
}

QString shortcutPsCommand(const QString& lnkPath, const QString& targetPath,
                          const QString& workingDir, const QString& iconPath,
                          const QString& description, const QString& args) {
    QStringList lines;
    lines << "$ws = New-Object -ComObject WScript.Shell";
    lines << "$s = $ws.CreateShortcut(" + psQuote(lnkPath) + ")";
    lines << "$s.TargetPath = " + psQuote(targetPath);
    QString wd = workingDir.isEmpty() ? QFileInfo(targetPath).absolutePath() : workingDir;
    lines << "$s.WorkingDirectory = " + psQuote(wd);
    if (!args.isEmpty())        lines << "$s.Arguments = " + psQuote(args);
    if (!iconPath.isEmpty())    lines << "$s.IconLocation = " + psQuote(iconPath);
    if (!description.isEmpty()) lines << "$s.Description = " + psQuote(description);
    lines << "$s.Save()";
    return lines.join("; ");
}

void writeUninstallRegistry(const QString& installDir, const QString& version,
                            const QString& uninstallExe) {
    QSettings reg("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + product(),
                  QSettings::NativeFormat);
    reg.setValue("DisplayName", product());
    reg.setValue("DisplayVersion", version);
    reg.setValue("DisplayIcon", uninstallExe);
    reg.setValue("Publisher", product());
    reg.setValue("InstallLocation", installDir);
    reg.setValue("UninstallString", "\"" + uninstallExe + "\" --uninstall");
    reg.setValue("NoModify", 1);
    reg.setValue("NoRepair", 1);
    reg.setValue("EstimatedSize", 553000);
}

QString startMenuDir() {
    QString appData = qEnvironmentVariable("APPDATA");
    return QDir::cleanPath(appData + "/Microsoft/Windows/Start Menu/Programs").replace('/', '\\');
}
QString desktopDir() {
    return QDir::toNativeSeparators(QStandardPaths::writableLocation(QStandardPaths::DesktopLocation));
}

QString uninstallRegistryKey() {
    return "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + product();
}

QStringList uninstallShortcutPaths() {
    QStringList s;
    s << startMenuDir() + "\\" + product() + ".lnk";
    s << startMenuDir() + "\\卸载 " + product() + ".lnk";
    s << desktopDir() + "\\" + product() + ".lnk";
    return s;
}

QString uninstallSelfDeleteScript(const QString& installDir, const QString& exeName,
                                  const QStringList& preserveDirs) {
    QString exe = exeName.isEmpty() ? QStringLiteral("HashMM.exe") : exeName;
    QStringList L;
    L << "@echo off";
    L << "chcp 65001 >nul";
    L << ":wait";
    // 等已装 exe 退出（否则删不掉正在运行的文件）
    L << QString("tasklist /fi \"imagename eq %1\" 2>nul | find /i \"%1\" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)").arg(exe);
    // 删控制面板卸载项
    L << QString("reg delete \"HKCU\\%1\" /f >nul 2>&1").arg(uninstallRegistryKey());
    // 删快捷方式
    for (const auto& lnk : uninstallShortcutPaths())
        L << QString("del /f /q \"%1\" >nul 2>&1").arg(lnk);
    if (!preserveDirs.isEmpty()) {
        // 保留数据夹：删安装目录下除 preserveDirs 外的所有子目录 + 根文件（链式 if，跳过每个保留夹）
        QString cond;
        for (const auto& d : preserveDirs)
            cond += QString("if /i not \"%%~nxD\"==\"%1\" ").arg(d);
        L << QString("for /d %%D in (\"%1\\*\") do %2rd /s /q \"%%D\" >nul 2>&1").arg(installDir, cond);
        L << QString("del /f /q \"%1\\*.*\" >nul 2>&1").arg(installDir);
    } else {
        L << QString("rd /s /q \"%1\" >nul 2>&1").arg(installDir);
    }
    // 删自身
    L << "del /f /q \"%~f0\" >nul 2>&1";
    return L.join("\r\n") + "\r\n";
}

} // namespace InstallEngine
