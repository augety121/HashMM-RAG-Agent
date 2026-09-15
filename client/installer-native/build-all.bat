@echo off
REM HashMM V2802 deterministic native release pipeline.
REM Produces one self-extracting installer plus SHA-256/release metadata.
cd /d "%~dp0"
setlocal enabledelayedexpansion
set "FAILED_STEP="
if not defined HASHMM_TIMESTAMP_URL set "HASHMM_TIMESTAMP_URL=http://timestamp.digicert.com"

echo ============================================================
echo   HashMM V2802 - verified native installer build
echo ============================================================

where python >nul 2>&1 || (set "FAILED_STEP=Python not found" & goto :fail)
where node >nul 2>&1 || (set "FAILED_STEP=Node.js not found" & goto :fail)
where npm >nul 2>&1 || (set "FAILED_STEP=npm not found" & goto :fail)

REM ---- locate Qt MinGW / compiler / cmake / ninja ----
set "QT_ROOT=C:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=D:\Qt"
if not exist "%QT_ROOT%" set "QT_ROOT=E:\Qt"
if not exist "%QT_ROOT%" (set "FAILED_STEP=Qt root not found; edit QT_ROOT" & goto :fail)

set "QTDIR="
for /d %%V in ("%QT_ROOT%\6.*") do if exist "%%V\mingw_64\bin\qmake.exe" set "QTDIR=%%V\mingw_64"
if not defined QTDIR (set "FAILED_STEP=Qt6 MinGW kit not found" & goto :fail)
set "MINGW="
for /d %%M in ("%QT_ROOT%\Tools\mingw*_64") do set "MINGW=%%M"
if not defined MINGW (set "FAILED_STEP=MinGW compiler not found" & goto :fail)
set "CMAKEBIN=%QT_ROOT%\Tools\CMake_64\bin"
set "NINJABIN=%QT_ROOT%\Tools\Ninja"
if not exist "%CMAKEBIN%\cmake.exe" set "CMAKEBIN="
if not exist "%NINJABIN%\ninja.exe" set "NINJABIN="
set "PATH=%QTDIR%\bin;%MINGW%\bin;%CMAKEBIN%;%NINJABIN%;%PATH%"
where cmake >nul 2>&1 || (set "FAILED_STEP=cmake not found" & goto :fail)
where gcc >nul 2>&1 || (set "FAILED_STEP=gcc not found" & goto :fail)
echo [OK] Qt=%QTDIR%

REM ---- 1/7 compile the native Qt installer ----
echo.
echo [1/7] Compiling native installer...
if exist "build-all" rmdir /s /q "build-all"
if defined NINJABIN (
  cmake -S . -B build-all -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
) else (
  cmake -S . -B build-all -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="%QTDIR%"
)
if errorlevel 1 (set "FAILED_STEP=CMake configure" & goto :fail)
cmake --build build-all
if errorlevel 1 (set "FAILED_STEP=native installer compile" & goto :fail)
if not exist "build-all\HashMM-Setup.exe" (set "FAILED_STEP=native installer output missing" & goto :fail)
"%QTDIR%\bin\windeployqt.exe" --release --no-translations --compiler-runtime build-all\HashMM-Setup.exe
if errorlevel 1 (set "FAILED_STEP=windeployqt" & goto :fail)

REM ---- 2/7 deterministic frontend dependencies + tests + production build ----
echo.
echo [2/7] Verifying and building frontend...
set "WEBUI=..\frontend-next"
pushd "%WEBUI%"
call npm ci --dry-run --ignore-scripts --prefer-offline --no-audit
if errorlevel 1 (popd & set "FAILED_STEP=frontend package-lock mismatch" & goto :fail)
if not exist "node_modules" call npm ci --prefer-offline --no-audit
if errorlevel 1 (popd & set "FAILED_STEP=frontend npm ci" & goto :fail)
call npm ls --depth=0
if errorlevel 1 (popd & set "FAILED_STEP=frontend dependency tree" & goto :fail)
call npm test
if errorlevel 1 (popd & set "FAILED_STEP=frontend tests" & goto :fail)
call npm run typecheck
if errorlevel 1 (popd & set "FAILED_STEP=frontend typecheck" & goto :fail)
if exist "out" rmdir /s /q "out"
if exist ".next" rmdir /s /q ".next"
call npm run build
if errorlevel 1 (popd & set "FAILED_STEP=frontend production build" & goto :fail)
popd
if not exist "%WEBUI%\out\index.html" (set "FAILED_STEP=frontend out\index.html missing" & goto :fail)

REM ---- 3/7 desktop dependencies, runtime, release gates, unpacked app ----
echo.
echo [3/7] Verifying desktop runtime and packaging app...
set "DESKTOP=..\desktop"
pushd "%DESKTOP%"
call npm ci --dry-run --ignore-scripts --prefer-offline --no-audit
if errorlevel 1 (popd & set "FAILED_STEP=desktop package-lock mismatch" & goto :fail)
if not exist "node_modules" call npm ci --prefer-offline --no-audit
if errorlevel 1 (popd & set "FAILED_STEP=desktop npm ci" & goto :fail)
call npm ls --depth=0
if errorlevel 1 (popd & set "FAILED_STEP=desktop dependency tree" & goto :fail)
python scripts\prepare-runtime.py --strict
if errorlevel 1 (popd & set "FAILED_STEP=Python runtime preparation" & goto :fail)
node tests-node\test_packaging_integrity.js
if errorlevel 1 (popd & set "FAILED_STEP=packaging integrity tests" & goto :fail)
node tests-node\test_observer_v4_brand.js
if errorlevel 1 (popd & set "FAILED_STEP=Observer V4 brand assets" & goto :fail)
node tests-node\test_project_vault.js
if errorlevel 1 (popd & set "FAILED_STEP=ProjectVault migration tests" & goto :fail)
node tests-node\test_backendmgr.js
if errorlevel 1 (popd & set "FAILED_STEP=backend manager tests" & goto :fail)
node services\test_capability-pack.js
if errorlevel 1 (popd & set "FAILED_STEP=capability pack tests" & goto :fail)
node tests-node\test_install_engine.js
if errorlevel 1 (popd & set "FAILED_STEP=installer engine tests" & goto :fail)
node tests-node\test_repo_intelligence.js
if errorlevel 1 (popd & set "FAILED_STEP=repository intelligence tests" & goto :fail)
node tests-node\test_worktree_manager.js
if errorlevel 1 (popd & set "FAILED_STEP=managed worktree tests" & goto :fail)
python scripts\verify-release.py --source-only
if errorlevel 1 (popd & set "FAILED_STEP=source release preflight" & goto :fail)
if exist "dist\win-unpacked" rmdir /s /q "dist\win-unpacked"
call npx electron-builder --win --dir
if errorlevel 1 (popd & set "FAILED_STEP=electron-builder" & goto :fail)
python scripts\verify-release.py --packaged dist\win-unpacked
if errorlevel 1 (popd & set "FAILED_STEP=packaged app verification" & goto :fail)
popd

REM ---- 4/7 assemble payload ----
echo.
echo [4/7] Assembling verified payload...
if exist "payload" rmdir /s /q "payload"
mkdir "payload"
xcopy "build-all\*" "payload\" /e /i /h /y >nul
if errorlevel 1 (set "FAILED_STEP=copy native payload" & goto :fail)
xcopy "%DESKTOP%\dist\win-unpacked" "payload\app\" /e /i /h /y >nul
if errorlevel 1 (set "FAILED_STEP=copy app payload" & goto :fail)
python "%DESKTOP%\scripts\verify-release.py" --packaged payload\app
if errorlevel 1 (set "FAILED_STEP=assembled payload verification" & goto :fail)

REM ---- 5/7 compile unique-temp self-extracting bootstrap ----
echo.
echo [5/7] Compiling self-extractor...
where windres >nul 2>&1 || (set "FAILED_STEP=windres not found" & goto :fail)
windres "build-all\hashmm-version.rc" -O coff -o "build-all\hashmm-version.o"
if errorlevel 1 (set "FAILED_STEP=self-extractor version resource" & goto :fail)
gcc bootstrap\bootstrap.c "build-all\hashmm-version.o" -o bootstrap\bootstrap.exe -O2 -mwindows -municode -lkernel32 -luser32 -lshell32
if errorlevel 1 (set "FAILED_STEP=self-extractor compile" & goto :fail)

REM ---- 6/7 stream-compress payload into the single EXE ----
echo.
echo [6/7] Packing single installer (streaming, deterministic)...
python "bootstrap\pack.py" --folder "payload" --stub "bootstrap\bootstrap.exe" --out "HashMM-Setup.exe"
if errorlevel 1 (set "FAILED_STEP=payload packing" & goto :fail)

REM ---- 7/7 optional Authenticode + mandatory SHA/release manifest ----
echo.
echo [7/7] Signing policy and release manifest...
if defined HASHMM_SIGN_PFX (
  if not exist "%HASHMM_SIGN_PFX%" (set "FAILED_STEP=HASHMM_SIGN_PFX file missing" & goto :fail)
  where signtool >nul 2>&1 || (set "FAILED_STEP=signtool not found" & goto :fail)
  if defined HASHMM_SIGN_PASSWORD (
    signtool sign /fd SHA256 /tr "%HASHMM_TIMESTAMP_URL%" /td SHA256 /f "%HASHMM_SIGN_PFX%" /p "%HASHMM_SIGN_PASSWORD%" "HashMM-Setup.exe"
  ) else (
    signtool sign /fd SHA256 /tr "%HASHMM_TIMESTAMP_URL%" /td SHA256 /f "%HASHMM_SIGN_PFX%" "HashMM-Setup.exe"
  )
  if errorlevel 1 (set "FAILED_STEP=Authenticode signing" & goto :fail)
  signtool verify /pa "HashMM-Setup.exe"
  if errorlevel 1 (set "FAILED_STEP=Authenticode verification" & goto :fail)
) else (
  if "%HASHMM_REQUIRE_SIGNING%"=="1" (set "FAILED_STEP=signing required but HASHMM_SIGN_PFX is unset" & goto :fail)
  echo [WARN] Installer is unsigned. Set HASHMM_SIGN_PFX and HASHMM_REQUIRE_SIGNING=1 for public release.
)
python "%DESKTOP%\scripts\verify-release.py" --artifact "HashMM-Setup.exe"
if errorlevel 1 (set "FAILED_STEP=artifact checksum/manifest" & goto :fail)

echo.
echo ============================================================
echo   DONE - verified outputs:
echo   %~dp0HashMM-Setup.exe
echo   %~dp0HashMM-Setup.exe.sha256
echo   %~dp0HashMM-Setup.release.json
echo ============================================================
if not "%HASHMM_NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo ============================================================
echo   BUILD FAILED: %FAILED_STEP%
echo   No installer from this run is approved for release.
echo ============================================================
if not "%HASHMM_NO_PAUSE%"=="1" pause
exit /b 1
