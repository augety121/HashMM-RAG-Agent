"""V88 防回归：requirements*.txt 必须纯 ASCII。

真机事故（Windows zh-CN）：pip 读 requirements 不假设 UTF-8——无 BOM 时按系统
locale（GBK/cp936）解码，文件里任何 UTF-8 中文注释都会直接
`UnicodeDecodeError: 'gbk' codec can't decode byte ...`，本地模式初始化全军覆没。
中文说明一律放 CONFIG.md「依赖清单中文对照」。

同时守住：无 BOM、核心包行还在（防误删）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _check_ascii(name: str) -> bytes:
    raw = (ROOT / name).read_bytes()
    assert raw, f"{name} 为空"
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 不应带 BOM"
    bad = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"{name} 含非 ASCII 字节（中文 Windows 的 pip 会按 GBK 解码炸掉）: 首个在偏移 {bad[0][0]}"
    return raw


def test_requirements_core_ascii_and_packages():
    raw = _check_ascii("requirements.txt").decode("ascii")
    pkgs = {ln.split("#")[0].strip().split(">=")[0].split("[")[0].lower()
            for ln in raw.splitlines() if ln.split("#")[0].strip()}
    for must in (
        "fastapi", "uvicorn", "numpy", "faiss-cpu", "openai", "pyjwt",
        "python-multipart", "python-pptx", "python-docx", "openpyxl",
    ):
        assert must in pkgs, f"requirements.txt 缺核心包 {must}（本地模式会装不全）"


def test_requirements_optional_ascii():
    raw = _check_ascii("requirements-optional.txt").decode("ascii")
    assert "beautifulsoup4" in raw
    assert "python-pptx" not in raw


if __name__ == "__main__":
    test_requirements_core_ascii_and_packages()
    test_requirements_optional_ascii()
    print("requirements ASCII 守卫: OK")
