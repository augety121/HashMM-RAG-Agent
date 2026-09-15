"""Error Recovery — diagnose tool failures and auto-fix.

Used by SmartAgent to automatically recover from common errors
during code execution and file operations.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

# Common package name → pip install name mapping
PACKAGE_MAP: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "magic": "python-magic",
    "jwt": "PyJWT",
    "lxml": "lxml",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "openpyxl": "openpyxl",
}

# Auto-import mapping for common abbreviations
AUTO_IMPORT_MAP: dict[str, str] = {
    "np": "import numpy as np",
    "pd": "import pandas as pd",
    "plt": "import matplotlib.pyplot as plt",
    "sns": "import seaborn as sns",
    "torch": "import torch",
    "nn": "import torch.nn as nn",
    "F": "import torch.nn.functional as F",
    "tf": "import tensorflow as tf",
    "cv2": "import cv2",
    "json": "import json",
    "os": "import os",
    "sys": "import sys",
    "re": "import re",
    "math": "import math",
    "Path": "from pathlib import Path",
    "defaultdict": "from collections import defaultdict",
    "Counter": "from collections import Counter",
    "dataclass": "from dataclasses import dataclass",
    "datetime": "from datetime import datetime",
    "timedelta": "from datetime import timedelta",
    "random": "import random",
    "time": "import time",
    "logging": "import logging",
    "typing": "import typing",
    "itertools": "import itertools",
    "functools": "import functools",
    "io": "import io",
    "csv": "import csv",
    "subprocess": "import subprocess",
    "requests": "import requests",
    "tqdm": "from tqdm import tqdm",
    "Image": "from PIL import Image",
}


@dataclass
class Recovery:
    """A recovery action to fix a tool execution failure."""
    action: str                     # "retry_with_fix" | "retry_after_install" | "llm_fix" | "skip"
    modified_args: dict = field(default_factory=dict)
    fix_steps: list[dict] = field(default_factory=list)  # pre-retry actions
    explanation: str = ""
    context: str = ""               # extra context for LLM fix


# Error patterns → recovery strategies
_PATTERNS: list[tuple[str, str]] = [
    (r"ModuleNotFoundError: No module named '(\w+)'", "auto_install"),
    (r"ImportError: No module named '(\w+)'", "auto_install"),
    (r"NameError: name '(\w+)' is not defined", "auto_import"),
    (r"FileNotFoundError:.*?'(.+?)'", "check_path"),
    (r"SyntaxError:", "syntax_fix"),
    (r"IndentationError:", "indent_fix"),
    (r"JSONDecodeError:", "json_fix"),
    (r"PermissionError:", "permission_skip"),
    (r"TimeoutError|timed out", "timeout_skip"),
    (r"UnicodeDecodeError:", "encoding_fix"),
    (r"ZeroDivisionError:", "zero_div_fix"),
]


def diagnose(tool: str, args: dict[str, Any], error: str) -> Recovery | None:
    """Analyze a tool execution error and return a recovery plan.

    Returns None if no automatic recovery is possible.
    """
    for pattern, strategy in _PATTERNS:
        m = re.search(pattern, error)
        if m:
            recovery = _get_recovery(strategy, m, tool, args, error)
            if recovery:
                return recovery
    return None


def _get_recovery(strategy: str, match: re.Match,
                  tool: str, args: dict[str, Any], error: str) -> Recovery | None:
    """Build a Recovery object based on the strategy."""

    if strategy == "auto_install":
        pkg = match.group(1)
        pip_name = PACKAGE_MAP.get(pkg, pkg)
        return Recovery(
            action="retry_after_install",
            modified_args=args,
            fix_steps=[{"tool": "run_shell",
                        "args": {"command": f"pip install {pip_name} -q"}}],
            explanation=f"自动安装缺失的包: {pip_name}",
        )

    elif strategy == "auto_import":
        name = match.group(1)
        if name in AUTO_IMPORT_MAP and tool == "execute_code":
            code = args.get("code", "")
            new_code = AUTO_IMPORT_MAP[name] + "\n" + code
            return Recovery(
                action="retry_with_fix",
                modified_args={**args, "code": new_code},
                explanation=f"自动添加: {AUTO_IMPORT_MAP[name]}",
            )

    elif strategy == "check_path":
        filepath = match.group(1)
        if tool in ("read_file", "str_replace") and filepath:
            return Recovery(
                action="llm_fix",
                context=f"文件不存在: {filepath}。请先用 list_files 或 file_tree 查看可用文件。",
                explanation="文件路径不存在",
            )

    elif strategy in ("syntax_fix", "indent_fix"):
        if tool == "execute_code":
            return Recovery(
                action="llm_fix",
                context=f"代码有语法错误：\n{error[:500]}\n请修正后重试。",
                explanation="代码语法错误，需LLM修正",
            )

    elif strategy == "encoding_fix":
        if tool in ("read_file", "execute_code"):
            code = args.get("code", "")
            if "open(" in code and "encoding" not in code:
                new_code = code.replace("open(", "open(encoding='utf-8', ")
                return Recovery(
                    action="retry_with_fix",
                    modified_args={**args, "code": new_code},
                    explanation="添加 encoding='utf-8' 参数",
                )

    elif strategy == "zero_div_fix":
        if tool == "execute_code":
            return Recovery(
                action="llm_fix",
                context=f"代码出现除零错误：\n{error[:500]}\n请添加零值检查后重试。",
                explanation="除零错误",
            )

    elif strategy in ("permission_skip", "timeout_skip"):
        return Recovery(
            action="skip",
            explanation=f"跳过不可恢复的错误: {strategy.replace('_skip', '')}",
        )

    return None


def inject_auto_imports(code: str) -> str:
    """Analyze code and prepend missing imports.

    Uses AST analysis when possible, falls back to regex.
    """
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # Can't analyze, return as-is

    # Collect existing imports
    existing: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                existing.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                existing.add(node.module.split(".")[0])
            for alias in node.names:
                existing.add(alias.asname or alias.name)

    # Collect used names
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used.add(node.value.id)

    # Find missing imports
    imports_to_add: list[str] = []
    for name in used - existing:
        if name in AUTO_IMPORT_MAP:
            imports_to_add.append(AUTO_IMPORT_MAP[name])

    if imports_to_add:
        header = "\n".join(sorted(set(imports_to_add)))
        return header + "\n\n" + code

    return code
