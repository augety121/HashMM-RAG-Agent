// UninstallWindow.cpp — 微信风卸载器实现（V102）。
// 卸载动作复用 install_engine 已有的 uninstallSelfDeleteScript（与原 main.cpp 的
// runUninstall 同款逻辑）：写自删 .bat 到临时目录 → 后台启动 → .bat 等本进程退出后
// 删安装目录（保留用户数据夹）与自身。默认保留 HashMM Files / local-backend。
// ⚠️ Qt 代码无法在本沙箱编译；请在 Windows + Qt6 构建确认。用的是稳定 Widgets API。
#include "UninstallWindow.h"
#include "wechat_style.h"
#include "running_guard.h"
#include "install_engine.h"

#include <QStackedWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QProgressBar>
#include <QMouseEvent>
#include <QApplication>
#include <QTimer>
#include <QStandardPaths>
#include <QFile>
#include <QDir>
#include <QProcess>

UninstallWindow::UninstallWindow(QString installDir, QWidget* parent)
    : QWidget(parent), installDir_(std::move(installDir)) {
    setWindowFlag(Qt::FramelessWindowHint);
    setAttribute(Qt::WA_TranslucentBackground, false);
    setFixedSize(440, 484);
    setObjectName("root");
    setStyleSheet(InstallerUI::wechatQss());

    stack_ = new QStackedWidget(this);
    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(0, 0, 0, 0);
    outer->addWidget(stack_);

    buildConfirm();
}

void UninstallWindow::mousePressEvent(QMouseEvent* e) {
    if (e->button() == Qt::LeftButton) dragPos_ = e->globalPosition().toPoint() - frameGeometry().topLeft();
}
void UninstallWindow::mouseMoveEvent(QMouseEvent* e) {
    if (e->buttons() & Qt::LeftButton) move(e->globalPosition().toPoint() - dragPos_);
}

void UninstallWindow::buildConfirm() {
    auto* page = new QWidget; page->setObjectName("root");
    auto* col = new QVBoxLayout(page);
    col->setContentsMargins(32, 14, 32, 26);
    col->setSpacing(0);

    { auto* bar = new QHBoxLayout; bar->addStretch();
      auto* clos = new QPushButton(QStringLiteral("\u2715")); clos->setObjectName("winBtn");
      clos->setFixedSize(28, 22);
      connect(clos, &QPushButton::clicked, this, &UninstallWindow::onCancel);
      bar->addWidget(clos); col->addLayout(bar); }
    col->addSpacing(10);

    { auto* tile = new QLabel(QStringLiteral("H")); tile->setObjectName("logoTile");
      tile->setAlignment(Qt::AlignCenter);
      auto* row = new QHBoxLayout; row->addStretch(); row->addWidget(tile); row->addStretch();
      col->addLayout(row); }
    col->addSpacing(18);

    { auto* t = new QLabel(QStringLiteral("\u5378\u8f7d HashMM\uff1f")); t->setObjectName("title");
      t->setAlignment(Qt::AlignCenter); col->addWidget(t); }
    col->addSpacing(8);

    { auto* s = new QLabel(QStringLiteral("\u5c06\u79fb\u9664\u7a0b\u5e8f\u6587\u4ef6\u3002\u4f60\u7684\u77e5\u8bc6\u5e93\u3001\u5de5\u4f5c\u533a\u4e0e\u8bbe\u7f6e\u4f1a\u4fdd\u7559\u3002"));
      s->setObjectName("sub"); s->setAlignment(Qt::AlignCenter); s->setWordWrap(true); col->addWidget(s); }
    col->addSpacing(16);

    { runningBanner_ = new QWidget; runningBanner_->setObjectName("runBanner");
      auto* h = new QHBoxLayout(runningBanner_); h->setContentsMargins(10, 8, 10, 8);
      auto* t = new QLabel(QStringLiteral("HashMM \u6b63\u5728\u8fd0\u884c\uff0c\u5378\u8f7d\u524d\u4f1a\u5148\u5e2e\u4f60\u5173\u95ed\u5b83\u3002"));
      t->setObjectName("runText"); h->addWidget(t); h->addStretch();
      runningBanner_->setVisible(false); col->addWidget(runningBanner_); col->addSpacing(12); }

    { auto* row = new QHBoxLayout; row->setSpacing(10);
      auto* cancel = new QPushButton(QStringLiteral("\u53d6\u6d88")); cancel->setObjectName("secondary");
      cancel->setCursor(Qt::PointingHandCursor);
      connect(cancel, &QPushButton::clicked, this, &UninstallWindow::onCancel);
      btnConfirm_ = new QPushButton(QStringLiteral("\u5378\u8f7d")); btnConfirm_->setObjectName("danger");
      btnConfirm_->setCursor(Qt::PointingHandCursor);
      connect(btnConfirm_, &QPushButton::clicked, this, &UninstallWindow::onConfirm);
      row->addWidget(cancel); row->addWidget(btnConfirm_); col->addLayout(row); }

    stack_->addWidget(page);
    stack_->setCurrentWidget(page);

    appRunning_ = InstallEngine::isAppRunning(appExeName_);
    if (runningBanner_) runningBanner_->setVisible(appRunning_);
    refreshConfirmText();
}

void UninstallWindow::refreshConfirmText() {
    if (btnConfirm_) btnConfirm_->setText(appRunning_ ? QStringLiteral("\u5173\u95ed\u5e76\u5378\u8f7d")
                                                      : QStringLiteral("\u5378\u8f7d"));
}

void UninstallWindow::buildProgress() {
    auto* page = new QWidget; page->setObjectName("root");
    auto* col = new QVBoxLayout(page); col->setContentsMargins(28, 40, 28, 28);
    col->addStretch();
    auto* t = new QLabel(QStringLiteral("\u6b63\u5728\u5378\u8f7d\u2026")); t->setObjectName("title");
    t->setAlignment(Qt::AlignCenter); col->addWidget(t); col->addSpacing(18);
    progress_ = new QProgressBar; progress_->setRange(0, 0); progress_->setTextVisible(false);
    col->addWidget(progress_); col->addSpacing(8);
    progressText_ = new QLabel(QStringLiteral("\u6b63\u5728\u79fb\u9664\u7a0b\u5e8f\u6587\u4ef6\u2026"));
    progressText_->setObjectName("sub"); progressText_->setAlignment(Qt::AlignCenter);
    col->addWidget(progressText_); col->addStretch();
    stack_->addWidget(page); stack_->setCurrentWidget(page);
}

void UninstallWindow::buildDone(bool ok, const QString& msg) {
    auto* page = new QWidget; page->setObjectName("root");
    auto* col = new QVBoxLayout(page); col->setContentsMargins(28, 40, 28, 28);
    col->addStretch();
    auto* t = new QLabel(ok ? QStringLiteral("\u5df2\u5378\u8f7d") : QStringLiteral("\u5378\u8f7d\u672a\u5b8c\u6210"));
    t->setObjectName("title"); t->setAlignment(Qt::AlignCenter); col->addWidget(t); col->addSpacing(8);
    auto* s = new QLabel(msg); s->setObjectName("sub"); s->setAlignment(Qt::AlignCenter); s->setWordWrap(true);
    col->addWidget(s); col->addSpacing(20);
    auto* done = new QPushButton(QStringLiteral("\u5b8c\u6210")); done->setObjectName("primary");
    done->setCursor(Qt::PointingHandCursor);
    connect(done, &QPushButton::clicked, this, &QWidget::close);
    col->addWidget(done); col->addStretch();
    stack_->addWidget(page); stack_->setCurrentWidget(page);
}

void UninstallWindow::onCancel() { close(); }

void UninstallWindow::onConfirm() {
    // 运行中先关
    if (appRunning_ || InstallEngine::isAppRunning(appExeName_)) {
        btnConfirm_->setEnabled(false);
        btnConfirm_->setText(QStringLiteral("\u6b63\u5728\u5173\u95ed HashMM\u2026"));
        qApp->processEvents();
        const bool closed = InstallEngine::closeRunningApp(appExeName_);
        appRunning_ = InstallEngine::isAppRunning(appExeName_);
        if (!closed || appRunning_) {
            if (runningBanner_) {
                runningBanner_->setVisible(true);
                if (auto* t = runningBanner_->findChild<QLabel*>(QStringLiteral("runText")))
                    t->setText(QStringLiteral("\u6ca1\u80fd\u81ea\u52a8\u5173\u95ed HashMM\uff0c\u8bf7\u624b\u52a8\u9000\u51fa\u540e\u518d\u70b9\u4e00\u6b21\u3002"));
            }
            btnConfirm_->setEnabled(true);
            refreshConfirmText();
            return;
        }
    }

    buildProgress();
    qApp->processEvents();

    // 写自删 .bat 到临时目录（不能写安装目录——一会要删它），后台启动它，然后本进程退出。
    // 默认保留用户数据夹（与"卸载保留数据"承诺一致）。
    QStringList preserve = { QStringLiteral("HashMM Files"), QStringLiteral("local-backend") };
    QString bat = InstallEngine::uninstallSelfDeleteScript(
        installDir_, InstallEngine::installedExeName(), preserve);

    QString tmpDir = QStandardPaths::writableLocation(QStandardPaths::TempLocation);
    QString batPath = QDir::cleanPath(tmpDir + "/hashmm-uninstall.bat");
    QFile f(batPath);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        buildDone(false, QStringLiteral("\u65e0\u6cd5\u5199\u5165\u5378\u8f7d\u811a\u672c\u3002"));
        return;
    }
    f.write(bat.toUtf8());
    f.close();

    QProcess::startDetached("cmd.exe", { "/c", batPath });
    buildDone(true, QStringLiteral("HashMM \u6b63\u5728\u5378\u8f7d\uff0c\u7a97\u53e3\u5173\u95ed\u540e\u5373\u5b8c\u6210\u3002\u4f60\u7684\u8d44\u6599\u4ecd\u4fdd\u7559\u3002"));
}
