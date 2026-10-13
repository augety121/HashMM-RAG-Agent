# HashMM App V200 变更说明

基线：V199。本轮做 App 三页面视觉重构 + 字体系统统一 + 混淆加固。功能链路不变。

## 1. 字体系统统一（ui/theme/Type.kt 重写）

- 现象：全 App 字号散落 19 种（10sp~36sp），同层级页面字号不一致，部分标题偏大（26/28/34/36sp 是桌面级巨字，移动端过大），观感"有的太大有的太小"。
- 改法：建立字体 Design Token（对标 iOS HIG / Material 3 落地值），一套语义化梯度替代魔法数字：
  - `AppFont` / `AppType`：screenTitle 22 / sectionTitle 17 / cardTitle 15 / body 15 / subtitle 13 / caption 12 / label 11（sp），行高统一 ~1.4 倍，相邻级差 1~2sp 不跳档。
  - 页面大标题 22sp 封顶（移动端不需要 28+ 巨字）；字重收敛到 Regular/Medium/SemiBold/Bold，去掉 ExtraBold（移动端偏重会糊）。
  - 接进 Material3 `Typography`，默认用 typography 的 M3 组件（Button/TopAppBar）自动对齐。
- 已迁移页面：ProfileScreen、ChatHomeScreen、RemoteControlScreen 的标题/正文字号全部改用 token（28 处引用），巨字号（24/26/28/34/36sp）清零。

## 2. 「我的」页面重构（ProfileScreen.kt）

- 条目行图标改为**淡染方块容器**（与工作台/高级能力页统一的视觉语言），不再是裸图标。
- 头像占位字母 28→22sp、昵称 21sp ExtraBold→22sp Bold、吉祥物人设卡 92→60dp（占屏更克制）。
- 条目标题 16→15sp、内边距收紧，整体更贴近大厂设置页的紧凑度。

## 3. 「对话」页面重构（ChatHomeScreen.kt）

- 抽屉大标题 26sp ExtraBold→22sp Bold、顶栏标题 18→17sp、空态标题 20→17sp，字重降到 Bold。
- 空态吉祥物 Hero 168→132dp（避免占屏过多），新建对话按钮字号统一。

## 4. 「远程」页面视觉统一（RemoteControlScreen.kt）

- 打开即自动连接（`LaunchedEffect connect()`，本就无需"开启"按钮，符合"打开就是远程"）。
- 设备选择屏（DevicePicker）：空态吉祥物 88→60dp，标题/中继模式/分区头字号统一到 token。
- 投屏内的功能性控件（键盘/鼠标/拖选）字号保持（这些是功能密度需要，非装饰）。

## 5. 混淆加固（proguard-rules.pro 强化）

- 从"宽松保留"改为"激进重命名 + 最小保留"：全量重命名 + 塌包（销毁包路径架构信息）+ 抹源文件名/行号 + 移除日志调用；只对 WebRTC/序列化/Ktor/Hilt 反射必需集合 keep。
- release 包（build-all.bat）自动生效，debug 不混淆。详见客户端 docs/安装包加密与代码保护方案.md。
- 提醒：R8 产出的 mapping.txt 务必离线保存、不进包（反混淆钥匙 + 崩溃还原依据）。

## 联动
- 配套客户端 V206：新增 build-encrypted.sh（PyArmor 加密打包）、未来路线图文档。详见客户端 CHANGELOG-V206.md。
