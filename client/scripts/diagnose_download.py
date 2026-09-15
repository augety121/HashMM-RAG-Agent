#!/usr/bin/env python3
"""下载404一键诊断。在项目根运行：python scripts/diagnose_download.py

它会告诉你：
1. 代码实际把文件存到哪个绝对目录
2. 那个目录里现在到底有哪些文件
3. 下载路由会从哪个目录找文件
4. 两者是否一致
"""
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from hashmm.api import database as db  # noqa: E402
from hashmm.api import tool_registry as TR  # noqa: E402

print("=" * 60)
print("HashMM 下载404 诊断")
print("=" * 60)
print(f"当前工作目录 (cwd): {os.getcwd()}")
print(f"DATA_ROOT (绝对):   {db.DATA_ROOT}")
print(f"CONV_FILES_ROOT:    {db.CONV_FILES_ROOT}")
print(f"tool_registry.CONV_FILES_ROOT: {TR.CONV_FILES_ROOT}")
print(f"两者一致: {db.CONV_FILES_ROOT.resolve() == TR.CONV_FILES_ROOT.resolve()}")
print()

# 列出所有对话目录及其文件
root = db.CONV_FILES_ROOT
if root.exists():
    print(f"对话文件根目录 {root} 下的内容：")
    for conv_dir in sorted(root.iterdir()):
        if conv_dir.is_dir():
            files = [p.name for p in conv_dir.iterdir() if p.is_file()]
            print(f"  {conv_dir.name}/  →  {files if files else '(空)'}")
else:
    print(f"⚠️  对话根目录不存在: {root}")
print()

# 检查是否有"第二套"目录（项目根下与 data 同级）
print("检查是否存在重复目录（双目录问题）：")
for name in ("conversations", "data/conversations"):
    p = os.path.abspath(name)
    exists = os.path.isdir(p)
    print(f"  {p}  存在={exists}")
print()
print("如果下载404，把以上全部输出发给我，即可定位。")
