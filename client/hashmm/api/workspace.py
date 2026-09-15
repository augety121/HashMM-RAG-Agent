"""Workspace Manager v13 — project-level file operations.

Capabilities:
  - ZIP upload and extraction
  - Project structure scanning + type detection
  - File tree generation
  - Workspace-wide text search
  - Precision line editing (str_replace, insert, read_range)
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, time, uuid, zipfile, difflib
from pathlib import Path
from typing import Any

from hashmm.api import database as _dbmod  # V308：动态读锚点，杜绝 reload 双脑（见 tool_registry 同处注释）
from hashmm.pipeline.resource_pipeline import SUPPORTED_FORMATS, TEXT_FORMATS, parse_resource
from hashmm.pipeline.parser import detect_format
MAX_ZIP_SIZE = 100 * 1024 * 1024  # 100MB
MAX_FILES_PER_PROJECT = 500


def get_workspace(conv_id: str) -> Path:
    d = _dbmod.CONV_FILES_ROOT / conv_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_within(child: Path, parent: Path) -> bool:
    """判断 child 是否等于 parent 或位于 parent 之下——按【路径语义】而非字符串前缀。

    V308 修 P0-1：原实现用 ``str(p).startswith(str(ws) + "/")``。该写法在 Windows 上
    永远为假（分隔符是 ``\\`` 不是 ``/``），导致工作区内的合法子文件被判非法，编辑/读取
    全部失败。改用 ``Path.relative_to``（等价于 3.9+ 的 ``is_relative_to``），跨平台正确，
    且天然规避 ``/ws`` vs ``/ws-evil`` 这类前缀误判。

    两侧均要求已 ``resolve()``（解析符号链接 + 规范化），故本函数同时挡住通过 symlink
    逃逸工作区的情况：symlink 目标一旦落在 ws 外，resolve 后的 relative_to 即失败。
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_path(conv_id: str, filepath: str) -> Path | None:
    """V50: 解析并校验文件路径必须落在本会话工作区内。

    这些函数即将暴露给 Agent 模型（filepath 由模型生成），必须防 `../` 穿越与 symlink 逃逸。
    返回 None 表示路径非法（越界/解析失败），调用方应返回 Error 给模型。
    """
    # 绝对路径直接拒绝：模型只应提供工作区【相对】路径，绝对路径是穿越信号。
    # （Windows 上 "C:\..."、POSIX 上 "/etc/..." 一律挡掉，不给 ws/abs 拼接留后门。）
    try:
        raw = str(filepath or "")
        if not raw.strip():
            return None
        if Path(raw).is_absolute() or (os.name == "nt" and re.match(r"^[A-Za-z]:", raw)):
            return None
        ws = get_workspace(conv_id).resolve()
        p = (ws / raw).resolve()
        return p if _is_within(p, ws) else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# ZIP Upload
# ═══════════════════════════════════════════════════════════════════

def upload_zip(zip_path: str, conv_id: str) -> dict:
    """Extract ZIP to conversation workspace. Returns project summary."""
    workspace = get_workspace(conv_id)
    zp = Path(zip_path)
    if not zp.exists():
        return {"error": f"ZIP 文件不存在: {zip_path}"}
    if zp.stat().st_size > MAX_ZIP_SIZE:
        return {"error": f"ZIP 文件过大 ({zp.stat().st_size / 1024 / 1024:.0f}MB > 100MB)"}

    extracted = 0
    skipped = 0
    try:
        with zipfile.ZipFile(zp) as zf:
            for info in zf.infolist():
                # Security: skip path traversal, hidden files, __MACOSX
                name = info.filename
                if '..' in name or name.startswith('/') or '__MACOSX' in name:
                    skipped += 1; continue
                if info.is_dir():
                    continue
                # Skip very large files
                if info.file_size > 10 * 1024 * 1024:
                    skipped += 1; continue

                target = workspace / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                extracted += 1

                if extracted >= MAX_FILES_PER_PROJECT:
                    break
    except zipfile.BadZipFile:
        return {"error": "ZIP 文件损坏"}

    project = scan_project(conv_id)
    project["extracted"] = extracted
    project["skipped"] = skipped
    return project


# ═══════════════════════════════════════════════════════════════════
# Project Scanning
# ═══════════════════════════════════════════════════════════════════

def scan_project(conv_id: str) -> dict:
    """Scan workspace, return file tree + project metadata."""
    workspace = get_workspace(conv_id)
    files = []
    total_size = 0
    total_lines = 0

    for item in sorted(workspace.rglob("*")):
        if not item.is_file():
            continue
        # Skip common noise
        rel = str(item.relative_to(workspace))
        if any(skip in rel for skip in ["node_modules/", "__pycache__/", ".git/", ".DS_Store", "venv/"]):
            continue

        sz = item.stat().st_size
        total_size += sz
        lines = 0
        if sz < 500000 and item.suffix in _TEXT_EXTENSIONS:
            try:
                lines = len(item.read_text(errors='replace').splitlines())
                total_lines += lines
            except Exception as _e:

                pass  # Silenced: see logs if needed
        files.append({
            "path": rel,
            "size": sz,
            "ext": item.suffix.lower(),
            "lines": lines if lines else None,
        })

    project_type = _detect_type(workspace)
    entry = _find_entry(workspace, project_type)

    return {
        "file_count": len(files),
        "total_size": total_size,
        "total_lines": total_lines,
        "project_type": project_type,
        "entry_point": entry,
        "files": files[:200],  # Limit for API response
    }


def file_tree(conv_id: str) -> str:
    """Generate tree-like text representation of workspace."""
    workspace = get_workspace(conv_id)
    if not workspace.exists():
        return "(空工作区)"

    lines = []
    _build_tree(workspace, workspace, lines, prefix="")
    return "\n".join(lines) if lines else "(空工作区)"


def _build_tree(root: Path, current: Path, lines: list, prefix: str, max_depth: int = 4):
    if len(lines) > 100:
        lines.append(f"{prefix}... (more files)")
        return
    items = sorted(current.iterdir(), key=lambda x: (x.is_file(), x.name))
    items = [i for i in items if i.name not in ('.git', 'node_modules', '__pycache__', 'venv', '.DS_Store')]

    for i, item in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        if item.is_dir():
            lines.append(f"{prefix}{connector}📁 {item.name}/")
            next_prefix = prefix + ("    " if is_last else "│   ")
            depth = len(item.relative_to(root).parts)
            if depth < max_depth:
                _build_tree(root, item, lines, next_prefix, max_depth)
        else:
            sz = item.stat().st_size
            sz_str = f"{sz / 1024:.1f}K" if sz > 1024 else f"{sz}B"
            icon = _file_icon(item.suffix)
            lines.append(f"{prefix}{connector}{icon} {item.name} ({sz_str})")


# ═══════════════════════════════════════════════════════════════════
# Precision Editing
# ═══════════════════════════════════════════════════════════════════

def read_file_range(conv_id: str, filepath: str, start: int = 1, end: int = 100) -> str:
    """Read specific line range from a file."""
    fpath = _safe_path(conv_id, filepath)
    if fpath is None:
        return f"Error: 非法路径 '{filepath}'（不允许越出会话工作区）"
    if not fpath.exists():
        return f"Error: 文件不存在: {filepath}"
    try:
        fmt = detect_format(fpath).lower()
        if fmt in SUPPORTED_FORMATS and fmt not in TEXT_FORMATS:
            resource = parse_resource(fpath, display_name=fpath.name)
            state = str(resource.get("parse_state") or "error")
            if state != "ready":
                detail = "；".join(str(item) for item in resource.get("warnings") or [])
                return f"Error: 文件当前不可读（{state}）: {detail}"
            lines = str(resource.get("text") or "").splitlines()
        else:
            lines = fpath.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception as e:
        return f"Error: 读取失败: {type(e).__name__}: {e}"

    total = len(lines)
    start = max(1, start)
    end = min(end, total)

    result_lines = []
    for i in range(start - 1, end):
        result_lines.append(f"{i + 1:>5} │ {lines[i]}")

    header = f"[{filepath}: 第 {start}-{end} 行，共 {total} 行]"
    return header + "\n" + "\n".join(result_lines)


def str_replace_in_file(conv_id: str, filepath: str, old_str: str, new_str: str) -> str:
    """Precision string replacement (like Claude Code's edit operation)."""
    fpath = _safe_path(conv_id, filepath)
    if fpath is None:
        return f"Error: 非法路径 '{filepath}'（不允许越出会话工作区）"
    if not fpath.exists():
        return f"Error: 文件不存在: {filepath}"

    content = fpath.read_text(encoding='utf-8')
    count = content.count(old_str)

    if count == 0:
        # Try fuzzy match (strip whitespace)
        stripped_old = old_str.strip()
        if content.count(stripped_old) == 1:
            old_str = stripped_old
            count = 1
        else:
            return f"Error: 未找到匹配的文本。请确认内容完全一致。"
    if count > 1:
        return f"Error: 找到 {count} 处匹配，请提供更长的上下文以唯一定位。"

    new_content = content.replace(old_str, new_str, 1)
    fpath.write_text(new_content, encoding='utf-8')

    # Generate diff
    diff = difflib.unified_diff(
        old_str.splitlines(keepends=True),
        new_str.splitlines(keepends=True),
        fromfile=f"a/{filepath}", tofile=f"b/{filepath}", n=2
    )
    diff_text = "".join(diff)
    return f"OK: 已替换 1 处。\n```diff\n{diff_text}\n```"


def patch_canvas_block(
    conv_id: str,
    filepath: str,
    old_html: str,
    new_html: str,
    block_id: str = "",
    operation: str = "replace",
) -> str:
    """Apply one exact HTML block mutation and persist a content-free receipt.

    This is the Agent-facing counterpart of the revision-checked HTTP patch
    endpoint.  A backend Agent turn is serialized by the active-run lease, so
    the exact old fragment is the compare-and-swap anchor: zero or multiple
    matches abort without writing.
    """
    fpath = _safe_path(conv_id, filepath)
    if fpath is None:
        return f"Error: 非法路径 '{filepath}'（不允许越出会话工作区）"
    if fpath.suffix.lower() not in {".html", ".htm"}:
        return "Error: canvas_block_patch 只允许修改 .html/.htm 画布"
    if not fpath.exists() or not fpath.is_file():
        return f"Error: 文件不存在: {filepath}"
    operation = str(operation or "replace").strip()
    if operation not in {"replace", "insert_after"}:
        return "Error: operation 仅支持 replace 或 insert_after"
    old_html = str(old_html or "")
    new_html = str(new_html or "")
    if not old_html:
        return "Error: old_html 不能为空"
    if len(old_html.encode("utf-8")) > 500_000 or len(new_html.encode("utf-8")) > 500_000:
        return "Error: 单个画布补丁片段不能超过 500KB"
    content = fpath.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_html)
    if count != 1:
        return f"Error: 画布块锚点匹配 {count} 次；必须提供唯一、完整的 old_html"
    replacement = new_html if operation == "replace" else old_html + new_html
    next_content = content.replace(old_html, replacement, 1)
    if len(next_content.encode("utf-8")) > 2_000_000:
        return "Error: 补丁后的画布超过 2MB 上限"
    base_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result_sha256 = hashlib.sha256(next_content.encode("utf-8")).hexdigest()
    if result_sha256 == base_sha256:
        return f"OK: 画布块无变化，版本仍为 {result_sha256}"
    tmp = fpath.with_suffix(fpath.suffix + ".patch.tmp")
    tmp.write_text(next_content, encoding="utf-8")
    os.replace(tmp, fpath)

    receipt_dir = get_workspace(conv_id) / ".canvas-patches"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{fpath.name}.json"
    history: list[dict] = []
    try:
        if receipt_path.exists():
            loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = [item for item in loaded if isinstance(item, dict)]
    except (OSError, ValueError, json.JSONDecodeError):
        history = []
    history.append({
        "schema": "hashmm.canvas-patch-receipt.v1",
        "patch_id": str(uuid.uuid4()),
        "block_id": re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(block_id or "canvas-root"))[:128],
        "operation": operation,
        "base_sha256": base_sha256,
        "result_sha256": result_sha256,
        "old_fragment_sha256": hashlib.sha256(old_html.encode("utf-8")).hexdigest(),
        "new_fragment_sha256": hashlib.sha256(new_html.encode("utf-8")).hexdigest(),
        "created_at": time.time(),
    })
    audit_tmp = receipt_path.with_suffix(".json.tmp")
    audit_tmp.write_text(json.dumps(history[-200:], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(audit_tmp, receipt_path)
    return (
        f"OK: 已对画布块 {block_id or 'canvas-root'} 应用 {operation}；"
        f"base={base_sha256[:12]} result={result_sha256[:12]}"
    )


def insert_after_line(conv_id: str, filepath: str, after_line: int, content: str) -> str:
    """Insert content after a specific line number."""
    fpath = _safe_path(conv_id, filepath)
    if fpath is None:
        return f"Error: 非法路径 '{filepath}'（不允许越出会话工作区）"
    if not fpath.exists():
        return f"Error: 文件不存在: {filepath}"

    lines = fpath.read_text(encoding='utf-8').splitlines()
    new_lines = content.splitlines()

    if after_line < 0 or after_line > len(lines):
        return f"Error: 行号 {after_line} 超出范围 (1-{len(lines)})"

    lines[after_line:after_line] = new_lines
    fpath.write_text("\n".join(lines) + "\n", encoding='utf-8')
    return f"OK: 在第 {after_line} 行后插入了 {len(new_lines)} 行"


def search_in_workspace(conv_id: str, pattern: str, max_results: int = 50) -> str:
    """Search text across all files in workspace."""
    workspace = get_workspace(conv_id)
    results = []

    for f in sorted(workspace.rglob("*")):
        if not f.is_file() or f.stat().st_size > 1000000:
            continue
        rel = str(f.relative_to(workspace))
        if any(skip in rel for skip in ["node_modules/", "__pycache__/", ".git/", "venv/"]):
            continue
        if f.suffix.lower() not in _TEXT_EXTENSIONS:
            continue

        try:
            for i, line in enumerate(f.read_text(errors='replace').splitlines(), 1):
                if pattern.lower() in line.lower():
                    results.append(f"  {rel}:{i}: {line.strip()[:150]}")
                    if len(results) >= max_results:
                        break
        except Exception as _e:

            pass  # Silenced: see logs if needed
        if len(results) >= max_results:
            break

    if not results:
        return f"未找到 '{pattern}'"
    return f"找到 {len(results)} 处匹配：\n" + "\n".join(results)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

_TEXT_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h', '.hpp',
    '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala', '.m', '.r',
    '.sh', '.bash', '.zsh', '.fish',
    '.html', '.css', '.scss', '.less', '.vue', '.svelte',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.env',
    '.md', '.txt', '.rst', '.csv', '.tsv', '.xml', '.sql',
    '.dockerfile', '.gitignore', '.editorconfig',
}

def _detect_type(workspace: Path) -> str:
    if (workspace / "requirements.txt").exists() or (workspace / "setup.py").exists() or (workspace / "pyproject.toml").exists():
        return "python"
    if (workspace / "package.json").exists():
        return "node"
    if (workspace / "pom.xml").exists() or (workspace / "build.gradle").exists():
        return "java"
    if (workspace / "Cargo.toml").exists():
        return "rust"
    if (workspace / "go.mod").exists():
        return "go"
    if (workspace / "CMakeLists.txt").exists() or (workspace / "Makefile").exists():
        return "cpp"
    return "unknown"

def _find_entry(workspace: Path, project_type: str) -> str | None:
    candidates = {
        "python": ["main.py", "app.py", "run.py", "server.py", "manage.py"],
        "node": ["index.js", "app.js", "server.js", "main.js", "src/index.ts"],
        "java": ["src/Main.java", "Main.java"],
    }
    for name in candidates.get(project_type, []):
        if (workspace / name).exists():
            return name
    return None

def _file_icon(ext: str) -> str:
    icons = {
        '.py': '🐍', '.js': '📜', '.ts': '📘', '.java': '☕', '.cpp': '⚙️', '.c': '⚙️',
        '.go': '🔷', '.rs': '🦀', '.html': '🌐', '.css': '🎨', '.json': '📋',
        '.md': '📝', '.txt': '📄', '.yml': '⚙️', '.yaml': '⚙️',
        '.pptx': '📊', '.docx': '📄', '.xlsx': '📈', '.pdf': '📕',
        '.png': '🖼', '.jpg': '🖼', '.svg': '🎨',
    }
    return icons.get(ext.lower(), '📄')


# ═══════════════════════════════════════════════════════════════════
# v17: Dependency Analysis
# ═══════════════════════════════════════════════════════════════════

def analyze_dependencies(conv_id: str) -> dict:
    """Analyze project dependencies: imports, missing packages, dependency graph."""
    workspace = get_workspace(conv_id)
    imports = set()
    local_modules = set()
    file_imports: dict[str, list[str]] = {}

    # Scan all Python files
    for f in workspace.rglob("*.py"):
        rel = str(f.relative_to(workspace))
        module_name = f.stem
        local_modules.add(module_name)

        try:
            content = f.read_text(encoding='utf-8', errors='replace')
            file_deps = []
            for line in content.splitlines():
                line = line.strip()
                # import xxx
                m = re.match(r'^import\s+([\w.]+)', line)
                if m:
                    pkg = m.group(1).split('.')[0]
                    imports.add(pkg)
                    file_deps.append(pkg)
                # from xxx import yyy
                m = re.match(r'^from\s+([\w.]+)\s+import', line)
                if m:
                    pkg = m.group(1).split('.')[0]
                    imports.add(pkg)
                    file_deps.append(pkg)
            file_imports[rel] = file_deps
        except Exception as _e:

            pass  # Silenced: see logs if needed
    import sys
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
        'os', 'sys', 'json', 're', 'math', 'time', 'datetime', 'pathlib',
        'collections', 'itertools', 'functools', 'typing', 'dataclasses',
        'subprocess', 'tempfile', 'shutil', 'copy', 'io', 'string',
        'random', 'hashlib', 'hmac', 'base64', 'struct', 'enum',
        'abc', 'contextlib', 'unittest', 'logging', 'argparse',
        'csv', 'sqlite3', 'http', 'urllib', 'socket', 'threading',
        'multiprocessing', 'queue', 'asyncio', 'concurrent',
    }

    external = imports - stdlib - local_modules - {''}
    missing = []

    for pkg in external:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    # Check for requirements.txt
    req_file = workspace / "requirements.txt"
    declared_deps = []
    if req_file.exists():
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                declared_deps.append(line.split('==')[0].split('>=')[0].split('<=')[0].strip())

    return {
        "local_modules": sorted(local_modules),
        "stdlib_imports": sorted(imports & stdlib),
        "external_imports": sorted(external),
        "missing_packages": sorted(missing),
        "declared_deps": declared_deps,
        "undeclared": sorted(external - set(declared_deps) - {''}),
        "file_imports": file_imports,
        "install_command": f"pip install {' '.join(missing)}" if missing else None,
    }
