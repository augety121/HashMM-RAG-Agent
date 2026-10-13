"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(path.join(__dirname, "..", "..", "installer-native", "InstallerWindow.cpp"), "utf8");

assert.match(source, /resize\(500, 590\)/);
assert.match(source, /setMinimumSize\(460, 540\)/);
assert.match(source, /titleLayout->addWidget\(minBtn\)/);
assert.match(source, /titleLayout->addWidget\(closeBtn\)/);
assert.doesNotMatch(source, /setGeometry\(width\(\) -/,
  "window controls must participate in layout at every DPI");
assert.match(source, /setAccessibleName\("关闭安装器"\)/);
assert.match(source, /安装位置与选项/);
assert.match(source, /bar_->setTextVisible\(true\)/);
assert.match(source, /agree_->setChecked\(false\)/,
  "first install consent must require an explicit user action");
assert.doesNotMatch(source, /agree_->setChecked\(true\)/);
assert.match(source, /QCheckBox::indicator \{ width:18px; height:18px; \}/);

console.log("test_v1500_installer_ui: passed");
