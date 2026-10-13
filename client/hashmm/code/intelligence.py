"""Bounded, content-addressed repository mapping.

Python uses the standard AST. JavaScript/TypeScript and common JVM/C-family
languages use intentionally conservative declaration/import patterns. The
result is a query-focused structural map, not a claim that every language was
fully parsed.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path
from typing import Any


REPO_MAP_SCHEMA = "hashmm.repository-map.v1"
_IGNORE = {
    ".git", ".hg", ".svn", "node_modules", ".next", "dist", "build",
    "__pycache__", ".venv", "venv", "coverage", ".pytest_cache",
}
_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".go", ".rs", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".cs", ".rb", ".php", ".swift", ".vue", ".svelte",
}
_DECL = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:async\s+)?"
    r"(?:class|interface|type|enum|function|def|struct|trait)\s+([A-Za-z_$][\w$]*)"
)
_IMPORT = re.compile(
    r"""(?mx)^\s*(?:
        import\s+(?:.+?\s+from\s+)?["']([^"']+)["'] |
        from\s+([\w.]+)\s+import |
        require\(["']([^"']+)["']\) |
        use\s+([A-Za-z_][\w:]*) |
        package\s+([\w.]+)
    )"""
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _python_structure(text: str) -> tuple[list[str], list[str], str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], [], "python_ast_failed"
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return symbols[:120], imports[:120], "python_ast"


def _generic_structure(text: str) -> tuple[list[str], list[str], str]:
    symbols = [match.group(1) for match in _DECL.finditer(text)]
    imports = [
        next((part for part in match.groups() if part), "")
        for match in _IMPORT.finditer(text)
    ]
    return symbols[:120], [item for item in imports if item][:120], "conservative_patterns"


def build_repository_map(
    root: str | Path,
    *,
    query: str = "",
    max_files: int = 2_000,
    max_file_bytes: int = 1_000_000,
    max_total_bytes: int = 32_000_000,
) -> dict[str, Any]:
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError("repository root does not exist")
    terms = {
        item.lower() for item in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", query)
        if len(item) > 1
    }
    files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total = 0
    for current, dirs, names in os.walk(base, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in _IGNORE
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(names):
            if len(files) >= max(1, min(max_files, 10_000)):
                break
            path = (Path(current) / name)
            if path.suffix.lower() not in _EXTENSIONS or path.is_symlink():
                continue
            resolved = path.resolve()
            if not _inside(base, resolved):
                continue
            try:
                size = resolved.stat().st_size
                if size > max_file_bytes or total + size > max_total_bytes:
                    errors.append({
                        "path": resolved.relative_to(base).as_posix(),
                        "reason": "size_budget",
                    })
                    continue
                data = resolved.read_bytes()
                if b"\x00" in data[:4096]:
                    continue
                text = data.decode("utf-8", errors="replace")
            except OSError as exc:
                errors.append({
                    "path": resolved.relative_to(base).as_posix(),
                    "reason": type(exc).__name__,
                })
                continue
            total += size
            if path.suffix.lower() in {".py", ".pyi"}:
                symbols, imports, parser = _python_structure(text)
            else:
                symbols, imports, parser = _generic_structure(text)
            relative = resolved.relative_to(base).as_posix()
            haystack = " ".join([relative, *symbols, *imports]).lower()
            score = sum(3 if term in relative.lower() else 1 for term in terms if term in haystack)
            files.append({
                "path": relative,
                "language": path.suffix.lower().lstrip("."),
                "bytes": size,
                "content_hash": _digest(data),
                "symbols": symbols,
                "imports": imports,
                "parser": parser,
                "query_score": score,
            })
        if len(files) >= max_files or total >= max_total_bytes:
            break
    focused = sorted(files, key=lambda item: (-item["query_score"], item["path"]))
    return {
        "schema": REPO_MAP_SCHEMA,
        "root_name": base.name,
        "query": str(query or "")[:500],
        "files": focused[: min(len(focused), 500)],
        "file_count": len(files),
        "bytes_scanned": total,
        "truncated": len(files) >= max_files or total >= max_total_bytes,
        "errors": errors[:100],
        "parser_contract": {
            "python": "ast",
            "other_languages": "conservative_patterns",
            "full_semantic_parse_claimed": False,
        },
    }
