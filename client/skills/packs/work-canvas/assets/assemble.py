#!/usr/bin/env python3
"""work-canvas 装配器：把 body.html 里的标记替换为真实资产，产出单文件画布。

用法:  python assets/assemble.py body.html out.html
标记:  [[PASTE canvas.css]]  [[PASTE canvas.js]]  [[IMG 相对路径]]
说明:  模型只写 body（省掉读入+复述 ~2 万字符资产的 token）；本脚本负责内联与
      轻量压缩（去 CSS 注释与多余空行），并把本地图片转 data: URI 内嵌。
      产物必须双击可离线打开、无任何外链请求（红线，见 SKILL.md）。
"""
import base64
import mimetypes
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _read(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def _min_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)          # 去注释
    css = re.sub(r"\n\s*\n+", "\n", css)                       # 折叠空行
    return css.strip()


def _img_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def assemble(body_path: Path, out_path: Path) -> None:
    html = body_path.read_text(encoding="utf-8")
    html = html.replace("[[PASTE canvas.css]]", _min_css(_read("canvas.css")))
    html = html.replace("[[PASTE canvas.js]]", _read("canvas.js").strip())

    def repl(m: re.Match) -> str:
        rel = m.group(1).strip()
        p = (body_path.parent / rel).resolve()
        if not p.is_file():
            sys.exit(f"[assemble] 图片不存在: {rel}")
        return _img_data_uri(p)

    html = re.sub(r"\[\[IMG\s+([^\]]+)\]\]", repl, html)

    leftovers = re.findall(r"\[\[(?:PASTE|IMG)[^\]]*\]\]", html)
    if leftovers:
        sys.exit(f"[assemble] 仍有未替换标记: {leftovers}")
    out_path.write_text(html, encoding="utf-8")
    print(f"[assemble] OK -> {out_path}  ({out_path.stat().st_size:,} bytes, 单文件自包含)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("用法: python assets/assemble.py body.html out.html")
    assemble(Path(sys.argv[1]), Path(sys.argv[2]))
