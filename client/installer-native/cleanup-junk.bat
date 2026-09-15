@echo off
REM ============================================================
REM  cleanup-junk.bat (V102) — 删掉仓库里的测试残留与误建目录。
REM  在 desktop 的上一级（仓库根）运行，或直接双击（它会 cd 到自己所在目录）。
REM  安全：只删 desktop\tmp 与 desktop\C: 这两处确认的垃圾。
REM ============================================================
cd /d "%~dp0"
setlocal

echo.
echo === 清理前 ===
if exist "desktop\tmp" ( echo desktop\tmp 存在 ) else ( echo desktop\tmp 不存在 )
if exist "desktop\C:" ( echo desktop\C: 存在 ) else ( echo desktop\C: 不存在 )
echo.

REM 68 个 hminst-* 安装测试残留目录
if exist "desktop\tmp" (
  echo 删除 desktop\tmp ...
  rmdir /s /q "desktop\tmp"
)

REM Windows 路径 bug 误建的字面量目录 desktop\C:\HashMMData
if exist "desktop\C:" (
  echo 删除 desktop\C: ...
  rmdir /s /q "desktop\C:"
)

echo.
echo === 清理后 ===
if exist "desktop\tmp" ( echo [!] desktop\tmp 仍在 ) else ( echo [OK] desktop\tmp 已删 )
if exist "desktop\C:" ( echo [!] desktop\C: 仍在 ) else ( echo [OK] desktop\C: 已删 )
echo.
echo 完成。这两处已加入 .gitignore，之后不会再被提交。
pause
