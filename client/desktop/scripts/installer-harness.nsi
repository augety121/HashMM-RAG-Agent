; desktop/scripts/installer-harness.nsi — installer.nsh 编译验证 harness（V100）
;
; 沙箱/CI 没 Windows、跑不动 electron-builder 全流程，但 makensis 有 Linux 原生版。
; 本文件按 electron-builder 生成 installer.nsi 的真实关键顺序，把 installer.nsh
; 的四个钩子（customHeader / customInstallMode / customWelcomePage /
; customPageAfterChangeDir / customFinishPage / customUnWelcomePage）放进与
; assistedInstaller.nsh 镜像一致的展开点，用 makensis 真编译——任何语法/资源/
; 变量引用错误在这里就炸，不等真机打包。要求 0 警告。
;
; 用法：makensis -DBUILD=<desktop/build 绝对路径> installer-harness.nsi

Unicode true
!define HM_HARNESS
!define APP_FILENAME "HashMM"
!define VERSION "0.0.0-harness"

!include "MUI2.nsh"

Name "HashMM"
OutFile "/tmp/hashmm-nsis-harness.exe"
InstallDir "$TEMP\hashmm-harness"
RequestExecutionLevel user

; ── electron-builder 按 yml 注入的品牌资源（手动等价注入） ──
!define MUI_ICON "${BUILD}/icon.ico"
!define MUI_UNICON "${BUILD}/icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${BUILD}/installerSidebar.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${BUILD}/uninstallerSidebar.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${BUILD}/installerHeader.bmp"
!define MUI_ABORTWARNING

; 自定义脚本用 ${BUILD_RESOURCES_DIR} 引用 build 资源（electron-builder 注入）；
; harness 手动等价指向 build 目录。
!define BUILD_RESOURCES_DIR "${BUILD}"

; multiUserUi.nsh 声明的变量（PAGE_INSTALL_MODE 流程读写），harness 等价声明，
; 让 customInstallMode 宏体里的 $isForceCurrentInstall 能编译。真模板里这两个变量
; 由 PAGE_INSTALL_MODE 的 pre 函数初始化，harness 无该页 → .onInit 里等价占位赋值，
; 避免 NSIS "变量从未设置" 告警（脚手架噪声，非 installer.nsh 问题）。
Var isForceCurrentInstall
Var isForceMachineInstall

; ── 被测对象 ──
!include "${BUILD}/installer.nsh"

; ── 镜像 assistedInstaller.nsh 的展开顺序 ──
!ifmacrodef customHeader
  !insertmacro customHeader
!endif

; customInstallMode 在真模板里由 PAGE_INSTALL_MODE 的 pre 函数展开（消费 flag
; 跳过选项页）；harness 无该页，用一个探针函数确保宏体能编译且 flag 变量在位。
Function hmHarnessProbeInstallMode
  !ifmacrodef customInstallMode
    !insertmacro customInstallMode
  !endif
FunctionEnd

; harness 初始化：占位赋值 multiUser 变量并引用上面的探针，把"未引用/未设置"
; 这两条脚手架告警消化掉（installer.nsh 本身 0 告警是测试硬指标）。
Function .onInit
  StrCpy $isForceMachineInstall "0"
  StrCpy $isForceCurrentInstall "0"
  ${If} $isForceMachineInstall == "never"
    Call hmHarnessProbeInstallMode
  ${EndIf}
FunctionEnd

!ifmacrodef customWelcomePage
  !insertmacro customWelcomePage
!endif

; 目录页在 V100 已关（yml allowToChangeInstallationDirectory:false），故 harness
; 也不插 MUI_PAGE_DIRECTORY —— 与真打包页面流一致。

!ifmacrodef customPageAfterChangeDir
  !insertmacro customPageAfterChangeDir
!endif

!insertmacro MUI_PAGE_INSTFILES

!ifmacrodef customFinishPage
  !insertmacro customFinishPage
!else
  !insertmacro MUI_PAGE_FINISH
!endif

!ifmacrodef customUnWelcomePage
  !insertmacro customUnWelcomePage
!else
  !insertmacro MUI_UNPAGE_WELCOME
!endif
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
SectionEnd
