@echo off
chcp 65001 >nul
rem ============================================================
rem  HashMM 桌面端 webui 热替换（V271）
rem  作用：把新构建的 frontend-next\out 覆盖到已安装桌面端的 resources\webui，
rem       不重装、不重打包，重启桌面端即生效。
rem  背景：打包后的桌面端从安装目录 resources\webui 加载界面（main.js→webuiDir），
rem       只更新源码/后端不会改变已装界面——"改了却没生效"的头号原因。
rem  用法：
rem    1) 先在源码目录构建：cd frontend-next && npm ci && npm run build
rem    2) 双击本脚本（或命令行传安装目录）：update-webui.bat "D:\Program Files\HashMM"
rem ============================================================
setlocal
set SRC=%~dp0..\..\frontend-next\out
if not exist "%SRC%\index.html" (
  echo [X] 未找到构建产物 %SRC%\index.html
  echo     先执行： cd frontend-next ^&^& npm ci ^&^& npm run build
  pause & exit /b 1
)
set DEST=%~1
if "%DEST%"=="" (
  for %%P in ("%LOCALAPPDATA%\Programs\HashMM" "%ProgramFiles%\HashMM" "%ProgramFiles(x86)%\HashMM") do (
    if exist "%%~P\resources" if not defined DEST set DEST=%%~P
  )
)
if "%DEST%"=="" (
  echo [X] 未找到 HashMM 安装目录。请把安装目录作为参数传入，例如：
  echo     update-webui.bat "D:\Program Files\HashMM"
  pause & exit /b 1
)
if not exist "%DEST%\resources" (
  echo [X] "%DEST%" 下没有 resources 目录，看起来不是 HashMM 安装目录。
  pause & exit /b 1
)
echo [i] 构建产物: %SRC%
echo [i] 安装目录: %DEST%
echo [i] 备份旧 webui 到 resources\webui.bak ...
if exist "%DEST%\resources\webui.bak" rmdir /s /q "%DEST%\resources\webui.bak"
if exist "%DEST%\resources\webui" move "%DEST%\resources\webui" "%DEST%\resources\webui.bak" >nul
echo [i] 复制新 webui ...
robocopy "%SRC%" "%DEST%\resources\webui" /e /nfl /ndl /njh /njs >nul
if errorlevel 8 (
  echo [X] 复制失败（可能需要管理员权限或先退出正在运行的 HashMM）。
  if exist "%DEST%\resources\webui.bak" move "%DEST%\resources\webui.bak" "%DEST%\resources\webui" >nul
  pause & exit /b 1
)
echo [√] 完成。重启 HashMM 桌面端即可看到新界面（旧版已备份为 webui.bak）。
pause
