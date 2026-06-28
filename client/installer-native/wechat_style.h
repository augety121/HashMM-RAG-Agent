#pragma once
// wechat_style.h — 安装器 / 卸载器共用样式表（V103.19 大厂简洁风改版），单一来源。
// 安装和卸载两个窗口共用同一套设计语言：克制的靛紫主色、Segoe UI+雅黑字体栈、
// 统一的圆角/按钮/输入/进度条规格，保证视觉完全一致。danger 仅卸载确认按钮用。
#include <QString>

namespace InstallerUI {

inline QString wechatQss() {
    return QString(R"(
        QWidget#root { background:#ffffff; }
        QLabel       { color:#26262e; font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif; }
        QLabel#title { font-size:20px; font-weight:600; color:#18181b; }
        QLabel#sub   { color:#8a8f99; font-size:12.5px; }

        QLabel#verPill {
            background:#eeecfe; color:#4f46c7; border-radius:9px;
            padding:2px 9px; font-size:11px; font-weight:600;
        }
        QLabel#logoTile {
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7c5cff,stop:1 #6457ec);
            color:#ffffff; border-radius:18px; font-size:34px; font-weight:700;
            min-width:66px; min-height:66px; max-width:66px; max-height:66px;
        }

        QPushButton#primary {
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #6e61f2,stop:1 #5a4de8);
            color:#ffffff; border:none; border-radius:11px;
            font-size:15px; font-weight:600; min-height:46px;
        }
        QPushButton#primary:hover    { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #7d72f5,stop:1 #6457ec); }
        QPushButton#primary:pressed  { background:#4d40d8; }
        QPushButton#primary:disabled { background:#dcd8f6; color:#f4f3fc; }

        /* 卸载确认主按钮：克制的红 */
        QPushButton#danger {
            background:#ef4d44; color:#ffffff; border:none; border-radius:11px;
            font-size:15px; font-weight:600; min-height:46px;
        }
        QPushButton#danger:hover   { background:#e23f37; }
        QPushButton#danger:pressed { background:#c8352e; }

        /* 次按钮（取消）：浅描边 */
        QPushButton#secondary {
            background:#ffffff; color:#3f3f46; border:1px solid #e4e4e7;
            border-radius:11px; font-size:14px; font-weight:500; min-height:46px;
        }
        QPushButton#secondary:hover { background:#f6f6f8; border-color:#d4d4da; }

        QCheckBox        { color:#8a8f99; font-size:12px; spacing:6px; }
        QCheckBox::indicator { width:15px; height:15px; }

        QLineEdit {
            border:1px solid #e4e4e7; border-radius:9px; padding:8px 11px;
            font-size:12.5px; color:#26262e; background:#fbfbfc;
        }
        QPushButton#ghost { background:transparent; color:#5a4de8; border:none; font-size:12px; }
        QPushButton#ghost:hover { color:#7d72f5; }

        QPushButton#winBtn { background:transparent; border:none; color:#a1a1aa; font-size:14px; border-radius:7px; }
        QPushButton#winBtn:hover { color:#18181b; background:#f2f2f6; }

        QProgressBar {
            border:none; background:#eef0f4; border-radius:4px; height:8px; text-align:center;
        }
        QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6e61f2,stop:1 #5a4de8); border-radius:4px; }

        QWidget#runBanner { background:#fff7ed; border:1px solid #fde9d3; border-radius:10px; }
        QLabel#runText    { color:#b45309; font-size:12px; }
    )");
}

} // namespace InstallerUI
