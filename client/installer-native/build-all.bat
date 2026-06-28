@echo off
REM ============================================================
REM  build-all.bat : produce a SINGLE self-extracting HashMM-Setup.exe
REM  (like WeChat: one exe, double-click to install. No zip.)
REM  Steps: compile Qt installer -> deploy Qt DLLs -> build app ->
REM         assemble payload -> compile Win32 stub -> pack into ONE exe.
REM  ONE CLICK. Just run this and wait.
REM ============================================================
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ============================================================
echo   HashMM - build SINGLE installer exe (one click)
echo   Do not close this window until you see DONE.
echo ============================================================
echo.

REM ---- locate Qt MinGW / compiler / cmake / ninja ----
set "QT_ROOT=C:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=D:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=E:\Qt"
if not exist "%QT_ROOT%" ( echo [ERROR] Qt root not found. Edit QT_ROOT at top. & pause & exit /b 1 )

set "QTDIR="
for /d %%V in ("%QT_ROOT%\6.*") do if exist "%%V\mingw_64\bin\qmake.exe" set "QTDIR=%%V\mingw_64"
if not defined QTDIR ( echo [ERROR] MinGW Qt6 not found ^(need %QT_ROOT%\6.x\mingw_64^). & pause & exit /b 1 )
echo [OK] Qt:    %QTDIR%

set "MINGW="
for /d %%M in ("%QT_ROOT%\Tools\mingw*_64") do set "MINGW=%%M"
if not defined MINGW ( echo [ERROR] MinGW compiler not found. & pause & exit /b 1 )
echo [OK] MinGW: %MINGW%

set "CMAKEBIN=%QT_ROOT%\Tools\CMake_64\bin"
set "NINJABIN=%QT_ROOT%\Tools\Ninja"
if not exist "%CMAKEBIN%\cmake.exe" set "CMAKEBIN="
if not exist "%NINJABIN%\ninja.exe" set "NINJABIN="
set "PATH=%QTDIR%\bin;%MINGW%\bin;%CMAKEBIN%;%NINJABIN%;%PATH%"
where cmake >nul 2>&1 || ( echo [ERROR] cmake not on PATH. & pause & exit /b 1 )
where gcc   >nul 2>&1 || ( echo [ERROR] gcc not on PATH ^(MinGW^). & pause & exit /b 1 )

REM ---- 1/6 compile the Qt installer ----
echo.
echo [1/6] Compiling Qt installer...
if exist "build-all" rmdir /s /q "build-all"
if defined NINJABIN (
  cmake -S . -B build-all -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
) else (
  cmake -S . -B build-all -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
)
if errorlevel 1 ( echo. & echo [FAIL] CMake configure - send me the red text. & pause & exit /b 1 )
cmake --build build-all
if errorlevel 1 ( echo. & echo [FAIL] Compile - send me the red text. & pause & exit /b 1 )
if not exist "build-all\HashMM-Setup.exe" ( echo [FAIL] Qt installer exe not produced. & pause & exit /b 1 )
echo [OK] Qt installer compiled.

REM ---- 2/6 deploy Qt DLLs next to the Qt installer ----
echo.
echo [2/6] Deploying Qt runtime DLLs...
"%QTDIR%\bin\windeployqt.exe" --release --no-translations --compiler-runtime build-all\HashMM-Setup.exe

REM ---- 3/6 build FRONTEND (next build) + app payload (electron-builder) ----
echo.
echo [3/6] Building frontend + app (a few minutes)...

REM  3a) Build the Next.js frontend -> frontend-next\out  (electron-builder bundles this as "webui").
REM      THIS step compiles UI changes. Without it the packaged app keeps the OLD frontend forever.
set "WEBUI=..\frontend-next"
if not exist "%WEBUI%\package.json" ( echo [ERROR] ..\frontend-next not found. & pause & exit /b 1 )
pushd "%WEBUI%"
if not exist "node_modules" ( echo    First time npm install for frontend ^(slow^)... & call npm install )
echo    Cleaning stale frontend build ^(out, .next^)...
if exist "out" rmdir /s /q "out"
if exist ".next" rmdir /s /q ".next"
echo    Running next build ^(this is what bakes your UI changes in^)...
call npm run build
popd
if not exist "%WEBUI%\out\index.html" ( echo. & echo [FAIL] frontend build produced no out\index.html - send me the red text above. & pause & exit /b 1 )
echo [OK] frontend built ^(frontend-next\out^).
echo.
echo    Building electron app...
set "DESKTOP=..\desktop"
if not exist "%DESKTOP%\package.json" ( echo [ERROR] ..\desktop not found. & pause & exit /b 1 )
pushd "%DESKTOP%"
if not exist "node_modules" ( echo    First time npm install ^(slow^)... & call npm install )
if not exist "node_modules\onnxruntime-node\package.json" ( echo    Installing onnxruntime-node ^(embedding service^)... & call npm install onnxruntime-node )
if not exist "node_modules\onnxruntime-node\package.json" ( echo    [WARN] onnxruntime-node missing - embedding toggle stays off. Check network/proxy. )
REM  3b) Packaging integrity gate: 每个启动期 require 必须在 electron-builder.yml 的 files 白名单里，
REM      否则打包后 app.asar 缺文件、启动即崩（双击没反应）。在出包前就红，不产出坏安装包。
echo    Checking packaging integrity ^(require vs files allowlist^)...
call node tests-node\test_packaging_integrity.js
if errorlevel 1 ( echo. ^& echo [FAIL] packaging integrity check failed - a startup require is missing from electron-builder.yml "files:" list. Add it there before shipping, or the packaged app crashes on launch. ^& popd ^& pause ^& exit /b 1 )
call npx electron-builder --win --dir
popd
if not exist "%DESKTOP%\dist\win-unpacked" ( echo [FAIL] app not built ^(win-unpacked missing^). & pause & exit /b 1 )
echo [OK] app built.

REM ---- 4/6 assemble payload\ = {Qt installer + Qt DLLs + app\} ----
echo.
echo [4/6] Assembling payload...
if exist "payload" rmdir /s /q "payload"
mkdir "payload"
xcopy "build-all\*" "payload\" /e /i /h /y >nul
xcopy "%DESKTOP%\dist\win-unpacked" "payload\app\" /e /i /h /y >nul
echo [OK] payload assembled ^(installer + DLLs + app^).

REM ---- 5/6 compile the Win32 self-extracting stub ----
echo.
echo [5/6] Compiling self-extractor stub...
gcc bootstrap\bootstrap.c -o bootstrap\bootstrap.exe -O2 -mwindows -municode -lkernel32 -luser32 -lshell32
if errorlevel 1 ( echo. & echo [FAIL] stub compile - send me the red text. & pause & exit /b 1 )
echo [OK] stub compiled.

REM ---- 6/6 pack payload into the stub -> single HashMM-Setup.exe ----
echo.
echo [6/6] Packing into a single exe ^(large, please wait^)...
if exist "HashMM-Setup.exe" del /f /q "HashMM-Setup.exe"
python "bootstrap\pack.py" --folder "payload" --stub "bootstrap\bootstrap.exe" --out "HashMM-Setup.exe"
if errorlevel 1 ( echo. & echo [FAIL] packing failed. & pause & exit /b 1 )

echo.
echo ============================================================
echo   DONE.
echo.
echo   THE installer to ship to users:  %~dp0HashMM-Setup.exe
echo   It is ONE exe. Users double-click it to install.
echo   ^(It self-extracts to %%TEMP%% then runs the installer UI.^)
echo ============================================================
echo.
pause
