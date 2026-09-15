@echo off
REM V335: the old ZIP/partial-package path could bypass runtime and release gates.
REM Keep this filename only as a compatibility entry point to the verified build.
cd /d "%~dp0"
echo [HashMM] make-package.bat now delegates to the verified build-all.bat pipeline.
call build-all.bat
exit /b %ERRORLEVEL%
