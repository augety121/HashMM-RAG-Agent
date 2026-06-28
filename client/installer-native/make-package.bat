@echo off
REM make-package.bat : deploy DLLs + build app + assemble + zip
REM (use this if you already compiled in Qt Creator; otherwise just run build-all.bat)
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ============================================================
echo   HashMM native installer - build full package
echo   (running; do not close until you see DONE)
echo ============================================================
echo.

set "QT_ROOT=C:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=D:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=E:\Qt"
set "QTBIN="
for /d %%V in ("%QT_ROOT%\6.*") do if exist "%%V\mingw_64\bin\windeployqt.exe" set "QTBIN=%%V\mingw_64\bin"
if not defined QTBIN (
  echo [ERROR] Qt MinGW not found under %QT_ROOT%\6.x\mingw_64
  echo         Edit QT_ROOT at the top of this file if your Qt is elsewhere.
  echo.
  pause
  exit /b 1
)
echo [OK] Qt: %QTBIN%

REM find exe in one-level build config folders (NOT deep Testing\Temporary dirs)
set "EXE="
for /d %%D in ("build\*") do if exist "%%D\HashMM-Setup.exe" set "EXE=%%D\HashMM-Setup.exe"
if not defined EXE if exist "build-all\HashMM-Setup.exe" set "EXE=build-all\HashMM-Setup.exe"
if not defined EXE if exist "build-cli\HashMM-Setup.exe" set "EXE=build-cli\HashMM-Setup.exe"
if not defined EXE (
  echo [ERROR] HashMM-Setup.exe not found.
  echo         Build it in Qt Creator first, or run build-all.bat.
  echo.
  pause
  exit /b 1
)
echo [OK] exe: !EXE!
for %%F in ("!EXE!") do set "EXEDIR=%%~dpF"

echo.
echo [1/4] Deploying Qt runtime DLLs...
"%QTBIN%\windeployqt.exe" --no-translations --compiler-runtime "!EXE!"
echo       Done.

echo.
echo [2/4] Assembling dist-installer\ ...
if exist "dist-installer" rmdir /s /q "dist-installer"
mkdir "dist-installer"
xcopy "!EXEDIR!*" "dist-installer\" /e /i /h /y >nul
echo       Copied exe and Qt DLLs.

echo.
echo [3/4] Building app payload (electron-builder, a few minutes)...
set "DESKTOP=..\desktop"
if not exist "%DESKTOP%\package.json" (
  echo       [SKIP] ..\desktop not found. Window opens but Install needs app files.
  goto :zip
)
pushd "%DESKTOP%"
if not exist "node_modules" ( echo       First time npm install ^(slow^)... & call npm install )
call npx electron-builder --win --dir
popd
if exist "%DESKTOP%\dist\win-unpacked" (
  if exist "dist-installer\app" rmdir /s /q "dist-installer\app"
  xcopy "%DESKTOP%\dist\win-unpacked" "dist-installer\app\" /e /i /h /y >nul
  echo       App payload placed in dist-installer\app\
) else (
  echo       [WARN] app not built. Window opens, Install lacks files.
)

:zip
echo.
echo [4/4] Zipping...
if exist "HashMM-Installer.zip" del /f /q "HashMM-Installer.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist-installer\*' -DestinationPath 'HashMM-Installer.zip' -Force"

echo.
echo ============================================================
echo   DONE.
echo   Test now:  double-click  dist-installer\HashMM-Setup.exe
echo   Ship:      HashMM-Installer.zip
echo ============================================================
echo.
pause
