// main.cpp — HashMM 原生安装器入口。
// 路由：--uninstall → 微信风卸载窗口（UninstallWindow，确认→进度→完成，卸载前先关进程，
//       默认保留用户数据）；否则显示安装窗口。
// V102：卸载从原来的 QMessageBox 升级为与安装器同款的 UninstallWindow；真正的自删逻辑
//       仍是 install_engine::uninstallSelfDeleteScript（在 UninstallWindow 内调用）。
#include <QApplication>
#include <QDir>
#include <QCoreApplication>
#include <QIcon>
#include "InstallerWindow.h"
#include "UninstallWindow.h"

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);
    app.setApplicationName("HashMM Setup");
    // Observer V4 is also the window/taskbar identity. The native resource
    // below is embedded in the executable, so Windows can show it before Qt
    // has finished constructing the first installer page.
    app.setWindowIcon(QIcon(":/icon.png"));

    if (app.arguments().contains("--uninstall")) {
        // 卸载经"卸载 HashMM.lnk → HashMM.exe --uninstall"调起，applicationDirPath() 即安装目录。
        QString installDir = QDir::toNativeSeparators(QCoreApplication::applicationDirPath());
        UninstallWindow win(installDir);
        win.show();
        return app.exec();
    }

    InstallerWindow win;
    win.show();
    return app.exec();
}
