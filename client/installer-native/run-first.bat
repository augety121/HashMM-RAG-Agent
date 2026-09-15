@echo off
REM run-first.bat : deploy Qt DLLs and open the installer window (quick test)
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ============================================================
echo   HashMM native installer - deploy Qt DLL ^& open window
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
  echo         Build it in Qt Creator first - green hammer at bottom-left.
  echo         Or just run build-all.bat to compile and package everything.
  echo.
  pause
  exit /b 1
)
echo [OK] exe: !EXE!

echo.
echo Deploying Qt runtime DLLs...
"%QTBIN%\windeployqt.exe" --no-translations --compiler-runtime "!EXE!"
echo [OK] DLLs deployed.

echo.
echo Opening the installer window...
start "" "!EXE!"
echo.
echo If the window appears, the native installer works.
echo Note: clicking Install needs app files - run build-all.bat or make-package.bat for that.
echo.
pause
