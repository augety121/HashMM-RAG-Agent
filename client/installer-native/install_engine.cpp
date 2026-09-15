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
#include <QCoreApplication>
#include <QVersionNumber>

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

InstallRecord readValidLastInstallRecord() {
    QFile f(lastInstallRecordPath());
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return {};
    auto doc = QJsonDocument::fromJson(f.readAll()); f.close();
    if (!doc.isObject()) return {};
    QString dir = doc.object().value("installDir").toString();
    if (dir.isEmpty() || !QFileInfo::exists(markerPath(dir))) return {};

    QString version;
    QFile marker(markerPath(dir));
    if (marker.open(QIODevice::ReadOnly | QIODevice::Text)) {
        const auto markerDoc = QJsonDocument::fromJson(marker.readAll());
        if (markerDoc.isObject()) version = markerDoc.object().value("version").toString();
    }
    if (version.isEmpty()) version = doc.object().value("version").toString();
    return {dir, version};
}

QString readValidLastInstall() {
    return readValidLastInstallRecord().installDir;
}

int compareVersions(const QString& left, const QString& right) {
    const QVersionNumber l = QVersionNumber::fromString(left.trimmed());
    const QVersionNumber r = QVersionNumber::fromString(right.trimmed());
    if (l.isNull() || r.isNull()) return 0;
    const int compared = QVersionNumber::compare(l, r);
    return compared < 0 ? -1 : (compared > 0 ? 1 : 0);
}

int countFiles(const QString& dir) {
    int n = 0;
    QDirIterator it(dir, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) { it.next(); ++n; }
    return n;
}

qint64 directoryBytes(const QString& dir) {
    qint64 total = 0;
    QDirIterator it(dir, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) {
        it.next();
        total += qMax<qint64>(0, it.fileInfo().size());
    }
    return total;
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

static bool dirEmpty(const QString& path) {
    return QDir(path).entryList(QDir::AllEntries | QDir::NoDotAndDotDot).isEmpty();
}

static bool validateStagedPayload(const QString& stage, QString* err) {
    const QStringList required = {
        QStringLiteral("HashMM.exe"),
        QStringLiteral("resources/app.asar"),
        QStringLiteral("resources/webui/index.html"),
        QStringLiteral("resources/backend/requirements.txt"),
        QStringLiteral("resources/backend/hashmm/__init__.py"),
        QStringLiteral("resources/runtime/runtime-info.json"),
        QStringLiteral("resources/runtime/python/python.exe"),
    };
    for (const auto& rel : required) {
        const QString full = QDir(stage).filePath(rel);
        QFileInfo info(full);
        if (!info.isFile() || info.size() <= 0) {
            if (err) *err = QStringLiteral("安装包缺少或损坏：") + rel;
            return false;
        }
    }
    return true;
}

bool copyTreeAtomic(const QString& from, const QString& to, const QString& version,
                    const QStringList& preserveDirs,
                    std::function<void(int)> onProgress, QString* err) {
    const QString dest = QDir::cleanPath(to);
    QFileInfo destInfo(dest);
    if (destInfo.exists() && !destInfo.isDir()) {
        if (err) *err = QStringLiteral("安装位置已存在同名文件：") + dest;
        return false;
    }
    if (destInfo.isDir() && !dirEmpty(dest) && !QFileInfo::exists(markerPath(dest))) {
        if (err) *err = QStringLiteral("目标目录非空且不是 HashMM 安装目录。请选择空目录，避免覆盖其它文件。");
        return false;
    }

    QDir parent(destInfo.absolutePath());
    if (!parent.exists() && !QDir().mkpath(parent.absolutePath())) {
        if (err) *err = QStringLiteral("无法创建安装目录的父目录：") + parent.absolutePath();
        return false;
    }
    const QString token = QString::number(QCoreApplication::applicationPid()) + "-" +
                          QString::number(QDateTime::currentMSecsSinceEpoch());
    const QString leaf = destInfo.fileName().isEmpty() ? QStringLiteral("HashMM") : destInfo.fileName();
    const QString stage = parent.filePath("." + leaf + ".stage-" + token);
    const QString backup = parent.filePath("." + leaf + ".backup-" + token);
    QDir(stage).removeRecursively();
    QDir(backup).removeRecursively();

    if (!copyTree(from, stage, onProgress, err)) {
        QDir(stage).removeRecursively();
        return false;
    }
    if (!validateStagedPayload(stage, err) || !writeMarker(stage, version)) {
        if (err && err->isEmpty()) *err = QStringLiteral("无法写入安装标记");
        QDir(stage).removeRecursively();
        return false;
    }

    const bool hadDest = QFileInfo::exists(dest);
    if (hadDest && !QDir().rename(dest, backup)) {
        if (err) *err = QStringLiteral("无法备份旧版本；请确认 HashMM 已完全退出：") + dest;
        QDir(stage).removeRecursively();
        return false;
    }
    if (!QDir().rename(stage, dest)) {
        if (hadDest) QDir().rename(backup, dest);
        if (err) *err = QStringLiteral("无法切换到新版本，旧版本已尝试恢复");
        QDir(stage).removeRecursively();
        return false;
    }

    QStringList moved;
    auto rollback = [&]() {
        for (auto it = moved.crbegin(); it != moved.crend(); ++it)
            QDir().rename(QDir(dest).filePath(*it), QDir(backup).filePath(*it));
        QDir(dest).removeRecursively();
        if (hadDest) QDir().rename(backup, dest);
    };
    if (hadDest) {
        for (const auto& name : preserveDirs) {
            const QString oldPath = QDir(backup).filePath(name);
            if (!QFileInfo::exists(oldPath)) continue;
            const QString newPath = QDir(dest).filePath(name);
            if (QFileInfo::exists(newPath) || !QDir().rename(oldPath, newPath)) {
                rollback();
                if (err) *err = QStringLiteral("迁移用户数据失败，已恢复旧版本：") + name;
                return false;
            }
            moved << name;
        }
        // Backup contains only superseded program files now. A cleanup failure
        // must not roll back an otherwise valid install or touch preserved data.
        QDir(backup).removeRecursively();
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
