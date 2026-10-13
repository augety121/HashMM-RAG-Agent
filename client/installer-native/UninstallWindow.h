#pragma once
// UninstallWindow.h — 微信风卸载器（V102）。与安装器同一套样式（wechat_style.h）。
// 三页：确认 -> 进度 -> 完成。卸载前先关进程（running_guard），默认保留用户资料
// （对应 electron-builder deleteAppDataOnUninstall:false）。
//
// 入口约定：你的 main 用 `--uninstall` 参数时构造本窗口（而非 InstallerWindow）。
// 卸载动作复用你 install_engine 里已有的移除逻辑（见 onConfirm 内的接入点注释）。
#include <QWidget>

class QStackedWidget;
class QLabel;
class QPushButton;
class QProgressBar;

class UninstallWindow : public QWidget {
    Q_OBJECT
public:
    explicit UninstallWindow(QString installDir, QWidget* parent = nullptr);

protected:
    // 无边框拖动（与安装器一致；若你已有 FramelessBase 可改为继承它）。
    void mousePressEvent(QMouseEvent*) override;
    void mouseMoveEvent(QMouseEvent*) override;

private slots:
    void onConfirm();   // 关进程 -> 卸载
    void onCancel();    // 关窗

private:
    void buildConfirm();
    void buildProgress();
    void buildDone(bool ok, const QString& msg);
    void refreshConfirmText();

    QString        installDir_;
    QString        appExeName_   = QStringLiteral("HashMM.exe");
    bool           appRunning_   = false;

    QStackedWidget* stack_       = nullptr;
    QWidget*        runningBanner_ = nullptr;
    QPushButton*    btnConfirm_  = nullptr;
    QProgressBar*   progress_    = nullptr;
    QLabel*         progressText_ = nullptr;

    QPoint          dragPos_;
};
