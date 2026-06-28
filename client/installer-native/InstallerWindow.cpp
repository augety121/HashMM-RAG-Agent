// InstallerWindow.cpp — 三态自绘安装窗口实现。观感对照 Electron 版（紫渐变按钮/圆角卡片）。
#include "InstallerWindow.h"
#include "install_engine.h"
#include "running_guard.h"   // V102: 运行中检测/关闭

#include <QStackedWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QPixmap>
#include <QPushButton>
#include <QCheckBox>
#include <QProgressBar>
#include <QLineEdit>
#include <QFileDialog>
#include <QMessageBox>
#include <QMouseEvent>
#include <QProcess>
#include <QFileInfo>
#include <QDir>
#include <QStorageInfo>
#include <QApplication>
#include <QCoreApplication>
#include <QTextBrowser>
#include <QFile>
#include <QDialog>
#include <QtConcurrent>
#include <QFutureWatcher>
#include <QPainterPath>
#include <QRegion>

static const char* kVersion = "1.6.0";

InstallerWindow::InstallerWindow(QWidget* parent) : QWidget(parent) {
    setWindowFlags(Qt::FramelessWindowHint | Qt::WindowSystemMenuHint);
    setAttribute(Qt::WA_TranslucentBackground, false);
    setFixedSize(480, 548);
    setWindowTitle("HashMM 安装");

    stack_ = new QStackedWidget(this);
    stack_->addWidget(buildWelcome());
    stack_->addWidget(buildProgress());
    stack_->addWidget(buildDone());

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->addWidget(stack_);

    applyStyle();

    // 自绘标题栏按钮：最小化 / 关闭（窗口无边框，必须自己加）。绝对定位在右上角，浮于内容之上。
    QPushButton* minBtn = new QPushButton(QString::fromUtf8("\u2014"), this);   // —
    minBtn->setObjectName("winmin");
    minBtn->setGeometry(width() - 74, 8, 30, 26);
    minBtn->setCursor(Qt::PointingHandCursor);
    connect(minBtn, &QPushButton::clicked, this, &QWidget::showMinimized);
    QPushButton* closeBtn = new QPushButton(QString::fromUtf8("\u00D7"), this); // ×
    closeBtn->setObjectName("winclose");
    closeBtn->setGeometry(width() - 40, 8, 30, 26);
    closeBtn->setCursor(Qt::PointingHandCursor);
    connect(closeBtn, &QPushButton::clicked, this, &QWidget::close);
    minBtn->raise();
    closeBtn->raise();

    installDir_ = InstallEngine::defaultInstallDir();
    pathBox_->setText(installDir_);

    // 可用空间
    QStorageInfo si(QFileInfo(installDir_).absolutePath());
    double freeGB = si.isValid() ? si.bytesAvailable() / (1024.0 * 1024 * 1024) : 0;
    spaceLabel_->setText(QString("安装所需空间：约 540 MB · 可用空间：%1 GB").arg(freeGB, 0, 'f', 1));

    detectExisting();
    refreshRunningState();   // V102: 启动即检测 HashMM 是否在跑
    QPainterPath rounded;
    rounded.addRoundedRect(0, 0, width(), height(), 16, 16);
    setMask(QRegion(rounded.toFillPolygon().toPolygon()));
}

void InstallerWindow::detectExisting() {
    QString prev = InstallEngine::readValidLastInstall();
    if (!prev.isEmpty()) {
        existing_ = true;
        installDir_ = prev;
        pathBox_->setText(prev);
        installedExe_ = QDir::cleanPath(prev + "/" + InstallEngine::installedExeName()).replace('/', '\\');
        badge_->setVisible(true);
        btnPrimary_->setText("启动 HashMM");
        btnPrimary_->setEnabled(true);
        btnReinstall_->setVisible(true);
        agree_->parentWidget()->setVisible(false);
        if (eulaLink_) eulaLink_->setVisible(true);   // V103.1: 启动态显示独立协议链接（点开弹窗）
    }
}

// V102: 按是否在跑，切运行中横幅 + 主按钮文案（仅安装态；已安装态主按钮是「启动」）。
void InstallerWindow::refreshRunningState() {
    appRunning_ = InstallEngine::isAppRunning(InstallEngine::installedExeName());
    if (runBanner_) {
        runBanner_->setText("HashMM 正在运行，安装前会先帮你关闭它。");
        runBanner_->setVisible(appRunning_);
    }
    if (btnPrimary_ && !existing_)
        btnPrimary_->setText(appRunning_ ? "关闭并安装" : "立即安装");
}

// V102: 在跑则先优雅关、再强制关（连子进程）。关不掉 → 提示手动退出并返回 false。
bool InstallerWindow::ensureNotRunning() {
    const QString exe = InstallEngine::installedExeName();
    if (!InstallEngine::isAppRunning(exe)) return true;
    if (btnPrimary_) { btnPrimary_->setEnabled(false); btnPrimary_->setText("正在关闭 HashMM…"); }
    qApp->processEvents();
    const bool closed = InstallEngine::closeRunningApp(exe);
    appRunning_ = InstallEngine::isAppRunning(exe);
    if (!closed || appRunning_) {
        if (runBanner_) {
            runBanner_->setText("没能自动关闭 HashMM，请手动退出后再点一次。");
            runBanner_->setVisible(true);
        }
        if (btnPrimary_) btnPrimary_->setEnabled(true);
        refreshRunningState();
        return false;
    }
    if (runBanner_) runBanner_->setVisible(false);
    return true;
}

QWidget* InstallerWindow::buildWelcome() {
    auto* w = new QWidget;
    auto* v = new QVBoxLayout(w);
    v->setContentsMargins(44, 32, 44, 26);

    auto* logo = new QLabel; logo->setObjectName("logo");
    logo->setPixmap(QPixmap(":/icon.png").scaled(84, 84, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    logo->setAlignment(Qt::AlignCenter);

    auto* name = new QLabel(QString("HashMM  <span style='font-size:13px;color:#c2c2cc'>%1</span>").arg(kVersion));
    name->setObjectName("name"); name->setAlignment(Qt::AlignCenter); name->setTextFormat(Qt::RichText);

    auto* tag = new QLabel("本地优先 · AI 工作台");
    tag->setObjectName("tag"); tag->setAlignment(Qt::AlignCenter);

    badge_ = new QLabel("✓ 检测到已安装"); badge_->setObjectName("badge");
    badge_->setAlignment(Qt::AlignCenter); badge_->setVisible(false);

    // V102: 「正在运行」提示条（默认隐藏；refreshRunningState 命中运行时显示）
    runBanner_ = new QLabel("HashMM 正在运行，安装前会先帮你关闭它。");
    runBanner_->setObjectName("runbanner");
    runBanner_->setAlignment(Qt::AlignCenter);
    runBanner_->setWordWrap(true);
    runBanner_->setVisible(false);

    btnPrimary_ = new QPushButton("立即安装"); btnPrimary_->setObjectName("primary");
    btnPrimary_->setEnabled(false);
    connect(btnPrimary_, &QPushButton::clicked, this, &InstallerWindow::onPrimary);

    btnReinstall_ = new QPushButton("重新安装到此位置"); btnReinstall_->setObjectName("link");
    btnReinstall_->setVisible(false);
    connect(btnReinstall_, &QPushButton::clicked, this, &InstallerWindow::onReinstall);

    auto* agreeRow = new QWidget;
    auto* ah = new QHBoxLayout(agreeRow); ah->setContentsMargins(0, 10, 0, 0); ah->setAlignment(Qt::AlignCenter);
    agree_ = new QCheckBox; ah->addWidget(agree_);
    auto* eula = new QLabel("已阅读并同意 <a href='#' style='color:#5a4de8;text-decoration:none'>《用户协议与隐私政策》</a>");
    eula->setTextFormat(Qt::RichText); eula->setObjectName("agreeText");
    connect(eula, &QLabel::linkActivated, this, [this](const QString&){ onEula(); });
    ah->addWidget(eula);
    connect(agree_, &QCheckBox::toggled, this, [this](bool on){ if (!existing_) btnPrimary_->setEnabled(on); });
    // 默认勾选「已阅读并同意」——让全新机器上「立即安装」按钮直接可点。
    // 之前别人电脑上没有旧安装、又没注意到这个不显眼的复选框，按钮就一直灰着点不了；
    // 协议链接仍可点开查看，用户也可取消勾选。
    agree_->setChecked(true);
    if (!existing_) btnPrimary_->setEnabled(true);

    auto* pathRow = new QWidget;
    auto* ph = new QHBoxLayout(pathRow); ph->setContentsMargins(0, 0, 0, 0);
    auto* pl = new QLabel("安装路径"); pl->setObjectName("pathlabel");
    pathBox_ = new QLineEdit; pathBox_->setReadOnly(true); pathBox_->setObjectName("pathbox");
    auto* browse = new QPushButton("浏览"); browse->setObjectName("browse");
    connect(browse, &QPushButton::clicked, this, &InstallerWindow::onBrowse);
    ph->addWidget(pl); ph->addWidget(pathBox_, 1); ph->addWidget(browse);

    spaceLabel_ = new QLabel("安装所需空间：约 540 MB"); spaceLabel_->setObjectName("space");

    // V103.1: 大厂安装器的做法——点击链接弹窗看完整协议，不内嵌占地方的滚动框。
    // 安装态：协议链接在勾选行(agreeRow)里；启动态(已安装)：用这个独立链接，点开同一弹窗。
    eulaLink_ = new QLabel("<a href='#' style='color:#5a4de8;text-decoration:none'>《用户协议与隐私政策》</a>");
    eulaLink_->setObjectName("eulalink"); eulaLink_->setAlignment(Qt::AlignCenter);
    eulaLink_->setTextFormat(Qt::RichText); eulaLink_->setVisible(false);
    connect(eulaLink_, &QLabel::linkActivated, this, [this](const QString&){ onEula(); });

    auto* badgeRow = new QHBoxLayout; badgeRow->addStretch(); badgeRow->addWidget(badge_); badgeRow->addStretch();
    v->addWidget(logo); v->addSpacing(12);
    v->addWidget(name); v->addSpacing(3); v->addWidget(tag); v->addSpacing(12); v->addLayout(badgeRow);
    v->addWidget(runBanner_);            // V102: 运行中提示条
    v->addStretch(1);
    v->addWidget(btnPrimary_); v->addSpacing(9); v->addWidget(btnReinstall_); v->addWidget(agreeRow);
    v->addWidget(eulaLink_);             // V103.1: 启动态可见的协议链接
    v->addSpacing(18);
    v->addWidget(pathRow); v->addSpacing(8); v->addWidget(spaceLabel_);
    return w;
}

QWidget* InstallerWindow::buildProgress() {
    auto* w = new QWidget;
    auto* v = new QVBoxLayout(w); v->setContentsMargins(46, 40, 46, 40);
    auto* logo = new QLabel; logo->setPixmap(QPixmap(":/icon.png").scaled(72, 72, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    logo->setAlignment(Qt::AlignCenter);
    auto* name = new QLabel("正在安装 HashMM…"); name->setObjectName("name2"); name->setAlignment(Qt::AlignCenter);
    progSub_ = new QLabel("正在准备文件"); progSub_->setObjectName("tag"); progSub_->setAlignment(Qt::AlignCenter);
    bar_ = new QProgressBar; bar_->setRange(0, 100); bar_->setValue(0); bar_->setTextVisible(false); bar_->setObjectName("bar");
    v->addStretch(1); v->addWidget(logo); v->addSpacing(16);
    v->addWidget(name); v->addWidget(progSub_); v->addSpacing(24); v->addWidget(bar_); v->addStretch(2);
    return w;
}

QWidget* InstallerWindow::buildDone() {
    auto* w = new QWidget;
    auto* v = new QVBoxLayout(w); v->setContentsMargins(46, 50, 46, 30);
    auto* check = new QLabel("✓"); check->setObjectName("doneIcon"); check->setAlignment(Qt::AlignCenter);
    check->setFixedSize(78, 78);
    auto* checkWrap = new QHBoxLayout; checkWrap->addStretch(); checkWrap->addWidget(check); checkWrap->addStretch();
    auto* name = new QLabel("安装完成"); name->setObjectName("name2"); name->setAlignment(Qt::AlignCenter);
    auto* tag = new QLabel("本地运行 · 数据保留 · 不动系统"); tag->setObjectName("tag"); tag->setAlignment(Qt::AlignCenter);
    donePath_ = new QLabel; donePath_->setObjectName("donePath"); donePath_->setAlignment(Qt::AlignCenter);
    auto* start = new QPushButton("开始使用"); start->setObjectName("primary");
    connect(start, &QPushButton::clicked, this, &InstallerWindow::onStart);
    v->addStretch(1); v->addLayout(checkWrap); v->addSpacing(18);
    v->addWidget(name); v->addWidget(tag); v->addWidget(donePath_); v->addStretch(1); v->addWidget(start);
    return w;
}

void InstallerWindow::onBrowse() {
    QString dir = QFileDialog::getExistingDirectory(this, "选择安装位置", installDir_);
    if (!dir.isEmpty()) { installDir_ = InstallEngine::normalizeInstallDir(dir); pathBox_->setText(installDir_); }
}

void InstallerWindow::onPrimary() {
    if (existing_) { launchInstalled(); return; }
    if (!agree_->isChecked()) return;
    if (!ensureNotRunning()) return;   // V102: 运行中 → 先关闭再装
    doInstall();
}
void InstallerWindow::onReinstall() {
    existing_ = false;
    if (!ensureNotRunning()) return;   // V102: 重装前同样先关
    doInstall();
}

void InstallerWindow::doInstall() {
    QString reason;
    if (!InstallEngine::validateInstallDir(installDir_, &reason)) {
        QMessageBox::warning(this, "无法安装", reason); return;
    }
    stack_->setCurrentIndex(1);

    // payload = 安装器 exe 同目录下的 app\ 子目录（electron-builder win-unpacked 放这里）
    QString payload = QDir::cleanPath(QCoreApplication::applicationDirPath() + "/app");
    if (!QFileInfo::exists(payload)) {
        QMessageBox::critical(this, "缺少程序文件", "未找到 app 目录（应与安装器同级）。");
        stack_->setCurrentIndex(0); return;
    }
    // app 目录存在但缺 HashMM.exe = 程序文件没打包进来。
    // 不拦的话 copyTree 会拷 0 个文件却返回成功，于是"点了没反应/装了打不开"——别人装不了的根因。
    if (!QFileInfo::exists(QDir::cleanPath(payload + "/HashMM.exe"))) {
        QMessageBox::critical(this, "缺少程序文件",
            "app 目录里没有 HashMM.exe（程序文件没打包进安装包）。\n请用 build-all.bat 重新打包，并确认 payload\\app 非空。");
        stack_->setCurrentIndex(0); return;
    }

    // 在后台线程拷贝，避免卡 UI
    auto* watcher = new QFutureWatcher<bool>(this);
    connect(watcher, &QFutureWatcher<bool>::finished, this, [this, watcher]() {
        bool ok = watcher->result(); watcher->deleteLater();
        if (!ok) { QMessageBox::critical(this, "安装失败", "拷贝程序文件失败。"); stack_->setCurrentIndex(0); return; }

        QString exeName = InstallEngine::installedExeName();
        installedExe_ = QDir::cleanPath(installDir_ + "/" + exeName).replace('/', '\\');
        QString iconPath = installedExe_;
        if (!QFileInfo::exists(installedExe_)) {
            QMessageBox::critical(this, "安装失败",
                "拷贝后未找到 " + exeName + "，程序文件可能没打包进安装包。请用 build-all.bat 重新打包。");
            stack_->setCurrentIndex(0); return;
        }

        progSub_->setText("正在创建快捷方式"); bar_->setValue(82);
        QString startDir = InstallEngine::startMenuDir();
        auto runPs = [](const QString& cmd) {
            QProcess::execute("powershell.exe", { "-NoProfile", "-NonInteractive", "-Command", cmd });
        };
        runPs(InstallEngine::shortcutPsCommand(startDir + "\\HashMM.lnk", installedExe_, installDir_, iconPath, "HashMM"));
        runPs(InstallEngine::shortcutPsCommand(InstallEngine::desktopDir() + "\\HashMM.lnk", installedExe_, installDir_, iconPath, "HashMM"));
        // 卸载快捷方式：开始菜单 + **安装目录内**（用户在 D:\hashmm 里就能直接看到「卸载 HashMM」，双击即卸载）
        runPs(InstallEngine::shortcutPsCommand(startDir + "\\卸载 HashMM.lnk", installedExe_, installDir_, iconPath, "卸载 HashMM", "--uninstall"));
        runPs(InstallEngine::shortcutPsCommand(installDir_ + "\\卸载 HashMM.lnk", installedExe_, installDir_, iconPath, "卸载 HashMM", "--uninstall"));

        progSub_->setText("正在写入卸载信息"); bar_->setValue(92);
        InstallEngine::writeUninstallRegistry(installDir_, kVersion, installedExe_);

        progSub_->setText("即将完成"); bar_->setValue(97);
        InstallEngine::writeMarker(installDir_, kVersion);
        InstallEngine::writeLastInstallRecord(installDir_, kVersion);

        bar_->setValue(100);
        donePath_->setText("已安装到 " + installDir_);
        stack_->setCurrentIndex(2);
    });
    QString dest = installDir_;
    watcher->setFuture(QtConcurrent::run([payload, dest, this]() -> bool {
        QString err;
        return InstallEngine::copyTree(payload, dest, [this](int pct){
            // 进度回调在工作线程——用 invokeMethod 安全更新 UI
            QMetaObject::invokeMethod(bar_, "setValue", Qt::QueuedConnection, Q_ARG(int, qMin(80, pct)));
        }, &err);
    }));
}

void InstallerWindow::launchInstalled() {
    if (installedExe_.isEmpty()) return;
    // Qt 原生安装器自身环境里本就没有 PORTABLE_*（它不是经 electron 便携壳启动的），
    // 所以直接 detached 启动已装 exe 即可，子进程不会继承到 PORTABLE_* 而误判 portable。
    QProcess::startDetached(installedExe_, QStringList(), QFileInfo(installedExe_).absolutePath());
    QApplication::quit();
}

void InstallerWindow::onStart() { launchInstalled(); }

void InstallerWindow::onEula() {
    QDialog dlg(this);
    dlg.setWindowFlags(Qt::FramelessWindowHint | Qt::Dialog);
    dlg.resize(680, 640);
    dlg.setStyleSheet(
        "QDialog{background:#fff;border:1px solid #e4e4e7;}"
        "QTextBrowser{border:none;background:#fff;font-family:'Segoe UI','Microsoft YaHei',sans-serif;font-size:13px;color:#3f3f46;padding:8px 16px;}"
        "#hdr{background:#fff;border-bottom:1px solid #f0f0f2;}"
        "#hdrTitle{font-size:14px;font-weight:600;color:#18181b;font-family:'Segoe UI','Microsoft YaHei',sans-serif;}"
        "#hdrClose{border:none;background:transparent;color:#a1a1aa;font-size:16px;border-radius:7px;}"
        "#hdrClose:hover{background:#f2f2f6;color:#18181b;}"
    );
    auto* v = new QVBoxLayout(&dlg);
    v->setContentsMargins(0, 0, 0, 0); v->setSpacing(0);
    // 自绘标题栏（去掉被系统主题染蓝的标题栏），与安装器整体一致
    auto* hdr = new QWidget; hdr->setObjectName("hdr"); hdr->setFixedHeight(46);
    auto* hh = new QHBoxLayout(hdr); hh->setContentsMargins(20, 0, 12, 0);
    auto* ht = new QLabel("用户协议与隐私政策"); ht->setObjectName("hdrTitle");
    auto* hc = new QPushButton(QString::fromUtf8("\u00D7")); hc->setObjectName("hdrClose");
    hc->setFixedSize(30, 30); hc->setCursor(Qt::PointingHandCursor);
    connect(hc, &QPushButton::clicked, &dlg, &QDialog::accept);
    hh->addWidget(ht); hh->addStretch(); hh->addWidget(hc);
    auto* tb = new QTextBrowser;
    tb->setOpenExternalLinks(true);
    QFile f(":/eula.html");
    if (f.open(QIODevice::ReadOnly)) { tb->setHtml(QString::fromUtf8(f.readAll())); f.close(); }
    else tb->setPlainText("HashMM 用户协议与隐私政策\n\n本地优先，数据存于本机，安装不修改系统配置，卸载默认保留数据。");
    v->addWidget(hdr); v->addWidget(tb);
    dlg.exec();
}

void InstallerWindow::applyStyle() {
    setStyleSheet(R"(
        QWidget { background:#ffffff; font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif; color:#26262e; }
        #name { font-size:25px; font-weight:600; color:#18181b; letter-spacing:0.2px; }
        #name2 { font-size:19px; font-weight:600; color:#18181b; }
        #tag { font-size:13px; color:#9094a0; }
        #badge { color:#4f46c7; background:#eeecfe; border-radius:13px; padding:6px 14px; font-size:12px; font-weight:500; }
        #primary { min-height:46px; border:none; border-radius:11px; color:#fff; font-size:15px; font-weight:600;
                   background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #6e61f2,stop:1 #5a4de8); }
        #primary:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #7d72f5,stop:1 #6457ec); }
        #primary:pressed { background:#4d40d8; }
        #primary:disabled { background:#dcd8f6; color:#f4f3fc; }
        #link { border:1px solid #e6e6ec; background:#ffffff; color:#5a4de8; font-size:14px; font-weight:500; border-radius:11px; min-height:44px; padding:0 18px; }
        #link:hover { border-color:#cfc8f7; background:#faf9ff; }
        #agreeText { font-size:12.5px; color:#8a8f99; }
        #pathlabel { font-size:12.5px; color:#82828f; }
        #pathbox { min-height:40px; border:1px solid #e4e4e7; border-radius:10px; background:#fbfbfc; padding:0 13px; font-size:12.5px; color:#52525b; }
        #browse { min-height:40px; border:1px solid #e4e4e7; border-radius:10px; background:#fff; padding:0 18px; font-size:13px; color:#52525b; font-weight:500; }
        #browse:hover { background:#f6f6f8; border-color:#d4d4da; }
        #space { font-size:11.5px; color:#aeaeba; }
        #runbanner { color:#b45309; background:#fff7ed; border:1px solid #fde9d3; border-radius:10px; padding:9px 13px; font-size:12px; }
        #eulalink { font-size:12.5px; }
        #bar { border:none; background:#eef0f4; border-radius:5px; max-height:8px; }
        #bar::chunk { border-radius:5px; background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6e61f2,stop:1 #5a4de8); }
        #donePath { font-size:12px; color:#aeaeba; }
        #doneIcon { font-size:40px; color:#fff; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #34d399,stop:1 #10b981); border-radius:39px; }
        #winmin, #winclose { border:none; background:transparent; color:#b0b0bb; font-size:15px; border-radius:7px; padding:0; }
        #winmin:hover { background:#f2f2f6; color:#555; }
        #winclose:hover { background:#ef4444; color:#fff; }
    )");
}

void InstallerWindow::mousePressEvent(QMouseEvent* e) {
    if (e->button() == Qt::LeftButton && e->position().y() < 60) {
        dragging_ = true; dragPos_ = e->globalPosition().toPoint() - frameGeometry().topLeft();
    }
}
void InstallerWindow::mouseMoveEvent(QMouseEvent* e) {
    if (dragging_ && (e->buttons() & Qt::LeftButton)) move(e->globalPosition().toPoint() - dragPos_);
}
void InstallerWindow::mouseReleaseEvent(QMouseEvent*) { dragging_ = false; }
