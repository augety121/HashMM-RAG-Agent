#pragma once
// InstallerWindow.h — 自绘无边框安装窗口（欢迎 / 进度 / 完成三态），观感对照 Electron 版。
#include <QWidget>

class QStackedWidget;
class QLabel;
class QPushButton;
class QCheckBox;
class QProgressBar;
class QLineEdit;
class QTextBrowser;

class InstallerWindow : public QWidget {
    Q_OBJECT
public:
    explicit InstallerWindow(QWidget* parent = nullptr);

protected:
    // 无边框窗口的拖动支持
    void mousePressEvent(QMouseEvent*) override;
    void mouseMoveEvent(QMouseEvent*) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void resizeEvent(QResizeEvent*) override;

private slots:
    void onBrowse();
    void onPrimary();        // 立即安装 / 启动 HashMM（已安装态）
    void onReinstall();
    void onStart();          // 完成页"开始使用"
    void onEula();
    void doInstall();
    void launchInstalled();

private:
    QWidget* buildWelcome();
    QWidget* buildProgress();
    QWidget* buildDone();
    void applyStyle();
    void detectExisting();
    // V102: 运行中检测 + 「关闭并安装」
    void refreshRunningState();      // 按是否在跑切横幅与主按钮文案
    bool ensureNotRunning();         // 在跑则先优雅→强制关闭；关不掉返回 false

    QStackedWidget* stack_ = nullptr;
    // welcome
    QLineEdit* pathBox_ = nullptr;
    QCheckBox* agree_ = nullptr;
    QPushButton* btnPrimary_ = nullptr;
    QPushButton* btnReinstall_ = nullptr;
    QLabel* badge_ = nullptr;
    QLabel* spaceLabel_ = nullptr;
    QLabel* runBanner_ = nullptr;    // V102: 「正在运行」琥珀色提示条
    bool appRunning_ = false;        // V102: 当前是否检测到 HashMM 在跑
    QLabel* eulaLink_ = nullptr;     // V103.1: 「点击看协议」独立链接（启动态用，安装态用勾选行里的链接）
    // progress
    QProgressBar* bar_ = nullptr;
    QLabel* progSub_ = nullptr;
    // done
    QLabel* donePath_ = nullptr;

    QString installDir_;
    QString installedExe_;
    QString installedVersion_;
    bool existing_ = false;
    bool upgradeAvailable_ = false;
    bool downgradeBlocked_ = false;
    QPoint dragPos_;
    bool dragging_ = false;
};
