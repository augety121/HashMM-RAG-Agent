/*
 * bootstrap.c - HashMM single-exe self-extractor (pure Win32, NO Qt, NO
 *               third-party libs). V102: compressed payload.
 *
 * The real installer is Qt and needs Qt DLLs, so it cannot be a single exe.
 * This tiny stub IS the single exe shipped to users. pack.py appends a single
 * DEFLATE .zip of {Qt installer + Qt DLLs + app/} after this stub.
 *
 * On run:
 *   1. read the 16-byte footer -> where the appended .zip begins
 *   2. copy the .zip bytes into a unique per-run directory under %TEMP%
 *   3. extract it with the OS-native unzip:
 *        a) %SystemRoot%\System32\tar.exe -xf payload.zip -C <dir>   (Win10 1803+)
 *        b) fallback: powershell Expand-Archive (any Win10+ / PS 5.1)
 *   4. launch the extracted Qt installer (<dir>\HashMM-Setup.exe)
 *
 * Why no in-stub inflate: keeping the stub dependency-free (no zlib link, no
 * vendored decoder) makes it trivially portable across MinGW/MSVC and avoids
 * shipping/compiling a decompressor. Windows already ships a zip extractor.
 *
 * Appended layout (after this exe's own bytes):
 *   [ zip bytes ............................ ]
 *   [ footer: int64 zipOffset | 8-byte magic ]   "HMSFXZ1\0"
 * All integers little-endian (x86/x64 + Python struct '<q').
 *
 * Build (MinGW): gcc bootstrap.c -o bootstrap.exe -O2 -mwindows -municode \
 *                    -lkernel32 -luser32 -lshell32
 */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

#define MAGIC8 "HMSFXZ1"   /* 7 chars + implicit terminating \0 == 8 bytes */

static void fail(const wchar_t* msg) {
    MessageBoxW(NULL, msg, L"HashMM Setup", MB_ICONERROR | MB_OK);
    ExitProcess(1);
}

static BOOL read_exact(HANDLE h, void* buf, DWORD n) {
    DWORD got = 0, rd;
    while (got < n) {
        if (!ReadFile(h, (char*)buf + got, n - got, &rd, NULL) || rd == 0)
            return FALSE;
        got += rd;
    }
    return TRUE;
}

/* Run a command line, wait for it, return its exit code (or -1 on spawn fail). */
static DWORD run_wait(wchar_t* cmdline, const wchar_t* cwd) {
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(NULL, cmdline, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, cwd, &si, &pi))
        return (DWORD)-1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, PWSTR cmd, int show) {
    (void)hi; (void)hp; (void)cmd; (void)show;

    /* ---- locate ourselves ---- */
    wchar_t self[MAX_PATH];
    if (!GetModuleFileNameW(NULL, self, MAX_PATH)) fail(L"Cannot locate self.");

    HANDLE hf = CreateFileW(self, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                            NULL, OPEN_EXISTING, 0, NULL);
    if (hf == INVALID_HANDLE_VALUE) fail(L"Cannot open self.");

    LARGE_INTEGER sz;
    if (!GetFileSizeEx(hf, &sz)) { CloseHandle(hf); fail(L"Cannot size self."); }

    /* ---- read 16-byte footer: [int64 zipOffset][8-byte magic] ---- */
    if (sz.QuadPart < 16) { CloseHandle(hf); fail(L"No payload appended."); }
    LARGE_INTEGER pos; pos.QuadPart = sz.QuadPart - 16;
    SetFilePointerEx(hf, pos, NULL, FILE_BEGIN);
    unsigned char footer[16];
    if (!read_exact(hf, footer, 16)) { CloseHandle(hf); fail(L"Cannot read footer."); }
    if (memcmp(footer + 8, MAGIC8, 8) != 0) {
        CloseHandle(hf);
        fail(L"Payload signature missing (not a packed HashMM setup, or corrupt).");
    }
    long long zipOffset = 0;
    memcpy(&zipOffset, footer, 8);
    long long zipLen = sz.QuadPart - 16 - zipOffset;
    if (zipOffset <= 0 || zipLen <= 0 || zipOffset >= sz.QuadPart) {
        CloseHandle(hf); fail(L"Payload offsets invalid.");
    }

    /* ---- prepare a UNIQUE temp directory.  The old fixed HashMMSetup path
            allowed two installers (or stale files) to race each other. ---- */
    wchar_t temp[MAX_PATH];
    DWORD tn = GetTempPathW(MAX_PATH, temp);
    if (tn == 0 || tn > MAX_PATH) { CloseHandle(hf); fail(L"Cannot get TEMP path."); }
    wchar_t workDir[MAX_PATH], zipPath[MAX_PATH], outDir[MAX_PATH];
    wchar_t uniquePath[MAX_PATH];
    if (!GetTempFileNameW(temp, L"HMM", 0, uniquePath)) {
        CloseHandle(hf); fail(L"Cannot allocate a unique TEMP path.");
    }
    DeleteFileW(uniquePath);
    if (!CreateDirectoryW(uniquePath, NULL)) {
        CloseHandle(hf); fail(L"Cannot create the unique TEMP directory.");
    }
    wcsncpy(workDir, uniquePath, MAX_PATH - 1);
    workDir[MAX_PATH - 1] = L'\0';
    _snwprintf(zipPath, MAX_PATH, L"%s\\payload.zip", workDir);
    _snwprintf(outDir, MAX_PATH, L"%s\\payload", workDir);
    CreateDirectoryW(outDir, NULL);

    /* ---- copy the appended zip bytes to payload.zip ---- */
    pos.QuadPart = zipOffset;
    SetFilePointerEx(hf, pos, NULL, FILE_BEGIN);
    HANDLE ho = CreateFileW(zipPath, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, NULL);
    if (ho == INVALID_HANDLE_VALUE) { CloseHandle(hf); fail(L"Cannot write temp zip."); }
    {
        char* buf = (char*)HeapAlloc(GetProcessHeap(), 0, 1 << 20); /* 1 MB */
        if (!buf) { CloseHandle(ho); CloseHandle(hf); fail(L"Out of memory."); }
        long long left = zipLen;
        while (left > 0) {
            DWORD want = (left > (1 << 20)) ? (1 << 20) : (DWORD)left;
            DWORD rd = 0, wr = 0;
            if (!ReadFile(hf, buf, want, &rd, NULL) || rd == 0) break;
            if (!WriteFile(ho, buf, rd, &wr, NULL) || wr != rd) break;
            left -= rd;
        }
        HeapFree(GetProcessHeap(), 0, buf);
        if (left != 0) { CloseHandle(ho); CloseHandle(hf); fail(L"Failed extracting payload."); }
    }
    CloseHandle(ho);
    CloseHandle(hf);

    /* ---- wipe any stale extraction first: a leftover app\ from a PREVIOUS
            build must NOT mask a missing/incomplete app\ in THIS package.
            (This is why an install can work on the dev box but no-op elsewhere.) ---- */
    {
        wchar_t rmcmd[1024];
        _snwprintf(rmcmd, 1024, L"cmd /c rd /s /q \"%s\"", outDir);
        run_wait(rmcmd, workDir);
    }
    CreateDirectoryW(outDir, NULL);

    /* ---- extract: try built-in tar.exe (fast), else PowerShell ---- */
    wchar_t sysRoot[MAX_PATH];
    GetWindowsDirectoryW(sysRoot, MAX_PATH);

    wchar_t cmd1[2048];
    _snwprintf(cmd1, 2048,
               L"\"%s\\System32\\tar.exe\" -xf \"%s\" -C \"%s\"",
               sysRoot, zipPath, outDir);
    DWORD rc = run_wait(cmd1, workDir);

    if (rc != 0) {
        /* Fallback: PowerShell Expand-Archive (works on any Win10+/PS5.1). */
        wchar_t cmd2[2048];
        _snwprintf(cmd2, 2048,
                   L"powershell -NoProfile -ExecutionPolicy Bypass -Command "
                   L"\"Expand-Archive -LiteralPath '%s' -DestinationPath '%s' -Force\"",
                   zipPath, outDir);
        rc = run_wait(cmd2, workDir);
        if (rc != 0) fail(L"Could not extract the installer payload.\n"
                          L"Please update Windows, or report this.");
    }

    /* ---- launch the extracted Qt installer ---- */
    wchar_t installer[MAX_PATH];
    _snwprintf(installer, MAX_PATH, L"%s\\HashMM-Setup.exe", outDir);
    if (GetFileAttributesW(installer) == INVALID_FILE_ATTRIBUTES)
        fail(L"Extracted, but installer binary not found.");

    /* Verify the PROGRAM payload actually extracted. Missing app\HashMM.exe is
       the root cause of "installer opens but Install does nothing" on a clean PC. */
    {
        wchar_t appExe[MAX_PATH];
        _snwprintf(appExe, MAX_PATH, L"%s\\app\\HashMM.exe", outDir);
        if (GetFileAttributesW(appExe) == INVALID_FILE_ATTRIBUTES)
            fail(L"Program files (app\\HashMM.exe) are missing from this setup package.\n"
                 L"Re-run build-all.bat and make sure payload\\app is NOT empty, then repack.");
    }

    /* Wait for the real installer, then remove this run's extraction.  Waiting
       prevents unbounded stale payloads and makes the bootstrap exit code useful. */
    wchar_t launchCmd[2 * MAX_PATH + 8];
    _snwprintf(launchCmd, 2 * MAX_PATH + 8, L"\"%s\"", installer);
    rc = run_wait(launchCmd, outDir);
    if (rc == (DWORD)-1) fail(L"Could not start the installer.");
    {
        wchar_t rmcmd[2 * MAX_PATH + 64];
        _snwprintf(rmcmd, 2 * MAX_PATH + 64, L"cmd /c rd /s /q \"%s\"", workDir);
        run_wait(rmcmd, temp);
    }
    return (int)rc;
}
