@echo off
REM build.bat : compile the installer from command line (auto-detect Qt MinGW)
REM Only needed if you do NOT use Qt Creator. If you use Qt Creator, skip this.
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ============================================================
echo   HashMM native installer - command-line build (MinGW)
echo ============================================================
echo.

set "QT_ROOT=C:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=D:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=E:\Qt"
if not exist "%QT_ROOT%" (
  echo [ERROR] Qt root not found. Edit QT_ROOT at the top of this file.
  pause
  exit /b 1
)

set "QTDIR="
for /d %%V in ("%QT_ROOT%\6.*") do if exist "%%V\mingw_64\bin\qmake.exe" set "QTDIR=%%V\mingw_64"
if not defined QTDIR (
  echo [ERROR] MinGW Qt6 not found ^(need %QT_ROOT%\6.x\mingw_64^).
  pause
  exit /b 1
)
echo [OK] Qt:    %QTDIR%

set "MINGW="
for /d %%M in ("%QT_ROOT%\Tools\mingw*_64") do set "MINGW=%%M"
if not defined MINGW (
  echo [ERROR] MinGW compiler not found ^(%QT_ROOT%\Tools\mingw*_64^).
  pause
  exit /b 1
)
echo [OK] MinGW: %MINGW%

set "CMAKEBIN=%QT_ROOT%\Tools\CMake_64\bin"
set "NINJABIN=%QT_ROOT%\Tools\Ninja"
if not exist "%CMAKEBIN%\cmake.exe" set "CMAKEBIN="
if not exist "%NINJABIN%\ninja.exe" set "NINJABIN="

set "PATH=%QTDIR%\bin;%MINGW%\bin;%CMAKEBIN%;%NINJABIN%;%PATH%"

where cmake >nul 2>&1
if errorlevel 1 (
  echo [ERROR] cmake not on PATH. Install CMake component in the Qt installer.
  pause
  exit /b 1
)

echo.
echo [1/3] CMake configure...
if defined NINJABIN (
  cmake -S . -B build-cli -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
) else (
  cmake -S . -B build-cli -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
)
if errorlevel 1 ( echo. & echo [FAIL] CMake configure error - send me the red text. & pause & exit /b 1 )

echo.
echo [2/3] Compile...
cmake --build build-cli
if errorlevel 1 ( echo. & echo [FAIL] Compile error - send me the red text. & pause & exit /b 1 )

echo.
echo [3/3] Deploy Qt DLLs...
"%QTDIR%\bin\windeployqt.exe" --no-translations --compiler-runtime build-cli\HashMM-Setup.exe

echo.
echo ============================================================
echo   DONE. exe: %~dp0build-cli\HashMM-Setup.exe
echo   Next: run make-package.bat to build app and zip the package.
echo ============================================================
echo.
pause
