cmake_minimum_required(VERSION 3.16)
project(HashMMSetup VERSION 17.0.2 LANGUAGES CXX)

# ── HashMM 原生安装器（C++/Qt6 Widgets）──────────────────────────────────────
# 对标微信/Marvis 的原生安装器：无 Electron 冷启动，点开即现窗口、启动瞬时。
# 安装逻辑严格对照已测的 desktop/installer/install-engine.js。
#
# 构建（Windows，需 Qt6 + MSVC 或 MinGW + CMake）：
#   cmake -S . -B build -DCMAKE_PREFIX_PATH="C:/Qt/6.6.0/msvc2019_64"
#   cmake --build build --config Release
#   windeployqt build/Release/HashMM-Setup.exe        # 拷 Qt 运行时 DLL
# 产物：build/Release/HashMM-Setup.exe（+ windeployqt 拷出的 Qt DLL）
# ─────────────────────────────────────────────────────────────────────────────

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

set(HASHMM_VERSION_RESOURCE "")
if (WIN32)
    enable_language(RC)
    configure_file(version.rc.in "${CMAKE_BINARY_DIR}/hashmm-version.rc" @ONLY)
    set(HASHMM_VERSION_RESOURCE "${CMAKE_BINARY_DIR}/hashmm-version.rc")
endif()

# Widgets（UI）+ Concurrent（后台拷贝线程 QtConcurrent::run）
find_package(Qt6 REQUIRED COMPONENTS Widgets Concurrent)

# WIN32 → 不弹控制台黑窗（GUI 子系统；MSVC 与 MinGW 都生效）
add_executable(HashMM-Setup WIN32
    main.cpp
    InstallerWindow.cpp
    InstallerWindow.h
    install_engine.cpp
    install_engine.h
    running_guard.cpp        # V102: 运行中检测/关闭
    running_guard.h
    UninstallWindow.cpp      # V102: 微信风卸载器
    UninstallWindow.h
    wechat_style.h           # V102: 安装/卸载共用样式
    resources.qrc
    ${HASHMM_VERSION_RESOURCE}
)

target_link_libraries(HashMM-Setup PRIVATE Qt6::Widgets Qt6::Concurrent)
target_compile_definitions(HashMM-Setup PRIVATE HASHMM_SETUP_VERSION="${PROJECT_VERSION}")

# Windows 上链接 Shell/Ole/Advapi（快捷方式 / 注册表）
if (WIN32)
    target_link_libraries(HashMM-Setup PRIVATE ole32 shell32 advapi32)
endif()

# 应用清单：asInvoker（装到用户目录不需要管理员）。仅 MSVC 用此链接器参数；
# MinGW 默认就是 asInvoker（无需清单），故不加，避免 MinGW 链接器不认 /MANIFESTUAC 报错。
if (MSVC)
    set_target_properties(HashMM-Setup PROPERTIES
        LINK_FLAGS "/MANIFESTUAC:\"level='asInvoker' uiAccess='false'\"")
    add_compile_options(/utf-8)   # MSVC：源码与执行字符集按 UTF-8（中文字符串）
endif()
# MinGW/GCC 默认 UTF-8 源码与执行字符集，无需额外标志。
