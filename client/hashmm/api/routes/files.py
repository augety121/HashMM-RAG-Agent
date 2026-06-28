"""File management routes — upload, download, preview, workspace listing.

Handles chat-level file uploads (parsed for LLM context) and
agent-created file downloads. Not to be confused with KB ingest
(routes/kb_ingest.py) which adds documents to the vector index.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from hashmm.api.auth import get_current_user


def _auth_and_safe_name(request: Request, filename: str) -> str:
    """v17 Phase 66: require login + reject path traversal for the legacy global
    file endpoints (preview/metadata/thumbnail). Filenames must be a bare
    basename — no '/', '\\', or '..'. Returns the safe basename."""
    from hashmm.api.auth import require_auth
    require_auth(request)
    safe = Path(filename).name
    if safe != filename or safe in ("", ".", ".."):
        raise HTTPException(400, "非法文件名")
    return safe
from hashmm.api import database as db
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.routes.files")

router = APIRouter(tags=["Files"])

_UPLOAD_DIR = Path("data/uploads")
_FILES_DIR = Path("data/files")


# ═══════════════════════════════════════════════════════════════════
# Upload (chat-level — parse for LLM context)
# ═══════════════════════════════════════════════════════════════════

@router.post("/api/upload",
             summary="上传文件并解析为文本",
             description="支持 PDF/DOCX/XLSX/CSV/图片/ZIP/纯文本，返回解析后的文本")
async def upload_file(file: UploadFile = File(...), request: Request = None,
                      analyze: str = Form("1")):
    content = await file.read()
    fname = file.filename or "unknown"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    # v10.0: File upload safety — whitelist extensions + size limit
    from hashmm.api.core.safety import validate_upload
    valid, reason = validate_upload(fname, len(content))
    if not valid:
        raise HTTPException(400, reason)

    # V103.3: 文件解析（PDF 抽取 / 图片视觉分析 / docx / Excel / zip）都可能较慢，
    # 且图片分析是视觉大模型调用——逐个放线程，避免上传一个大文件就冻住整个后端。结果不变。
    import asyncio
    text = ""
    if ext == "pdf":
        text = await asyncio.to_thread(_extract_pdf, content, fname)
    elif ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
        # V86: analyze=0 → 跳过泛图片分析（问答栏截屏即贴即发，定向解读由
        # /stream 的 vision 前置步骤按用户问题做，避免上传时白等一次大模型）。
        text = "" if analyze == "0" else await asyncio.to_thread(_analyze_image, content, fname)
    elif ext == "docx":
        text = await asyncio.to_thread(_extract_docx, content, fname)
    elif ext in ("xlsx", "csv"):
        text = await asyncio.to_thread(_extract_tabular, content, fname, ext)
    elif ext == "zip":
        text = await asyncio.to_thread(_extract_zip, content, fname)
    else:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = f"[二进制文件: {fname}, {len(content)} bytes]"

    # Persist to disk for Agent tool access
    try:
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]', '_', fname)[:80]
        (_UPLOAD_DIR / safe_name).write_bytes(content)
    except Exception as _e:
        log_suppressed(logger, _e)

    user = get_current_user(request) if request else None
    if user:
        db.audit(user["uid"], user["sub"], "upload", fname)

    return {"filename": fname, "text": text[:15000]}


# ═══════════════════════════════════════════════════════════════════
# Download & Preview (agent-created files)
# ═══════════════════════════════════════════════════════════════════

@router.get("/api/files/{filename}",
            summary="下载文件")
async def download_file(filename: str, request: Request, conv: str = ""):
    """Download a workspace file. Requires auth.

    - admin: may download any file.
    - regular user: when a conversation is given, it must belong to them.
    Filenames are basename-only to prevent path traversal.
    """
    from hashmm.api.auth import require_auth
    user = require_auth(request)
    uid = user.get("uid") or user.get("sub")
    is_admin = user.get("role") == "admin"

    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(400, "非法文件名")

    from hashmm.api.tool_registry import CONV_FILES_ROOT

    if conv:
        if not is_admin:
            from hashmm.api import database as db
            cobj = db.get_conversation(conv)
            if not cobj or cobj.get("user_id") != uid:
                raise HTTPException(403, "无权访问该文件")
        filepath = CONV_FILES_ROOT / conv / safe_name
        if filepath.exists() and filepath.is_file():
            return FileResponse(filepath, filename=safe_name)
        # fall through to legacy lookup if not found in conv dir

    # legacy global file (requires auth; admins always allowed)
    filepath = _FILES_DIR / safe_name
    if filepath.exists() and filepath.is_file():
        return FileResponse(filepath, filename=safe_name)

    # V86: chat-upload fallback — 问答栏附件（截屏/图片）落盘在 _UPLOAD_DIR，
    # 用户消息里的图片卡按原始文件名请求本路由，这里兜底（先原名、再净化名，
    # 与 upload_file 的落盘净化规则一致）。安全口径与上面的 legacy 查找相同：
    # 已登录即可、仅 basename。
    filepath = _UPLOAD_DIR / safe_name
    if filepath.exists() and filepath.is_file():
        return FileResponse(filepath, filename=safe_name)
    _sanitized = re.sub(r'[^\w.\-]', '_', safe_name)[:80]
    if _sanitized and _sanitized != safe_name and Path(_sanitized).name == _sanitized:
        filepath = _UPLOAD_DIR / _sanitized
        if filepath.exists() and filepath.is_file():
            return FileResponse(filepath, filename=safe_name)

    # admin convenience: search all conversation dirs for the file
    if is_admin and CONV_FILES_ROOT.exists():
        for d in CONV_FILES_ROOT.iterdir():
            cand = d / safe_name
            if cand.is_file():
                return FileResponse(cand, filename=safe_name)

    raise HTTPException(404, "文件不存在")


# NOTE (v17 Phase 67): the JSON code-preview handler that used to live here
# registered the SAME path as the HTML document-preview handler below
# (`/api/files/{filename}/preview`). Two handlers on one path → the first
# registered wins, which shadowed the HTML one and broke DOCX preview in the UI
# (ArtifactPanel fetches this path expecting HTML). The global JSON code-preview
# was unused by any client (the per-conversation route
# `/api/conversations/{conv_id}/files/{filename}/preview` covers code preview),
# so it was removed; `file_preview` (HTML) below is now the sole handler.


@router.get("/api/workspace",
            summary="列出工作区文件（Agent 创建的文件）")
async def list_workspace(request: Request):
    """List the caller's own workspace files (strict per-user isolation, admin
    included). Admins use the dedicated admin view to see everyone's files.

    Files are stored per conversation (CONV_FILES_ROOT/<conv_id>/).
    """
    from hashmm.api.auth import require_auth
    user = require_auth(request)
    uid = user.get("uid") or user.get("sub")

    from hashmm.api.tool_registry import CONV_FILES_ROOT
    from hashmm.api import database as db

    def _entry(f, cid):
        st = f.stat(); sz = st.st_size
        return {"filename": f.name, "size": sz,
                "size_str": f"{sz/1024:.1f}K" if sz < 1048576 else f"{sz/1048576:.1f}M",
                "modified": int(st.st_mtime * 1000),
                "download_url": f"/api/files/{f.name}?conv={cid}",
                "ext": f.suffix.lstrip("."), "conv_id": cid}

    files = []
    try:
        conv_ids = {c["id"] for c in db.list_conversations(uid, limit=500)}
    except Exception:
        conv_ids = set()
    for cid in conv_ids:
        cdir = CONV_FILES_ROOT / cid
        if cdir.exists():
            for f in cdir.iterdir():
                if f.is_file():
                    files.append(_entry(f, cid))
    files.sort(key=lambda x: -x["modified"])
    return {"files": files[:100]}


# ═══════════════════════════════════════════════════════════════════
# File extractors (private)
# ═══════════════════════════════════════════════════════════════════

def _extract_pdf(content: bytes, fname: str) -> str:
    """Extract text from PDF. Strategy: pdftotext → pdfplumber → PyPDF2."""
    # Method 1: pdftotext (system tool — fast, layout-preserving)
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = subprocess.run(
            ["pdftotext", "-layout", tmp_path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        os.unlink(tmp_path)
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            pages = text.split("\f")
            if len(pages) > 1:
                marked = "\n\n".join(
                    f"[第{i+1}页]\n{p.strip()}"
                    for i, p in enumerate(pages[:30]) if p.strip()
                )
            else:
                marked = text
            return f"[PDF: {fname}, {len(pages)}页]\n\n{marked}"
    except FileNotFoundError as _e:
        log_suppressed(logger, _e)
    except Exception as e:
        logger.warning(f"pdftotext failed: {e}")
        try:
            os.unlink(tmp_path)
        except Exception as _e:
            log_suppressed(logger, _e)

    # Method 2: pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = []
            for i, page in enumerate(pdf.pages[:30]):
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(f"[第{i+1}页]\n{t}")
            if pages:
                return f"[PDF: {fname}, {len(pdf.pages)}页]\n\n" + "\n\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")

    # Method 3: PyPDF2 / pypdf
    for reader_cls_name in ["pypdf.PdfReader", "PyPDF2.PdfReader"]:
        try:
            mod, cls_name = reader_cls_name.rsplit(".", 1)
            import importlib
            m = importlib.import_module(mod)
            PdfReader = getattr(m, cls_name)
            reader = PdfReader(io.BytesIO(content))
            pages = []
            for i, page in enumerate(reader.pages[:30]):
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(f"[第{i+1}页]\n{t}")
            if pages:
                return f"[PDF: {fname}, {len(reader.pages)}页]\n\n" + "\n\n".join(pages)
        except (ImportError, AttributeError):
            continue
        except Exception as e:
            logger.warning(f"{reader_cls_name} failed: {e}")

    return (
        f"[PDF: {fname}] 无法提取文本。请安装 poppler-utils：\n"
        f"  apt-get install -y poppler-utils\n"
        f"或安装 Python 包：\n"
        f"  pip install pdfplumber --break-system-packages"
    )


def _extract_docx(content: bytes, fname: str) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return f"[Word: {fname}]\n\n{text}" if text else f"[空文档: {fname}]"
    except ImportError:
        return f"[Word文件: {fname}。请安装 python-docx]"
    except Exception as e:
        return f"[Word文件读取失败: {e}]"


def _extract_tabular(content: bytes, fname: str, ext: str) -> str:
    try:
        if ext == "csv":
            text = content.decode("utf-8", errors="replace")
            lines = text.strip().split("\n")
            return f"[CSV: {fname}, {len(lines)}行]\n\n" + "\n".join(lines[:100])
        else:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            sheets = []
            for name in wb.sheetnames[:5]:
                ws = wb[name]
                rows = []
                for row in ws.iter_rows(max_row=50, values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
                sheets.append(f"[Sheet: {name}]\n" + "\n".join(rows))
            return f"[Excel: {fname}]\n\n" + "\n\n".join(sheets)
    except ImportError:
        return f"[{ext.upper()}文件: {fname}。请安装 openpyxl]"
    except Exception as e:
        return f"[文件读取失败: {e}]"


def _extract_zip(content: bytes, fname: str) -> str:
    import zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        extracted = []
        text_parts = []
        for name in names[:50]:
            if name.endswith("/") or "__MACOSX" in name:
                continue
            info = zf.getinfo(name)
            if info.file_size > 10 * 1024 * 1024:
                extracted.append(f"  {name} ({info.file_size // 1024}K) [跳过:太大]")
                continue
            try:
                data = zf.read(name)
                safe = re.sub(r'[^\w.\-]', '_', Path(name).name)[:80]
                if safe:
                    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    (_UPLOAD_DIR / safe).write_bytes(data)
                    extracted.append(f"  {name} ({info.file_size // 1024}K) → uploads/{safe}")
                ext = Path(name).suffix.lower()
                if ext in (".py", ".js", ".ts", ".md", ".txt", ".json",
                           ".yaml", ".yml", ".csv", ".html", ".css"):
                    try:
                        t = data.decode("utf-8", errors="replace")[:2000]
                        text_parts.append(f"--- {name} ---\n{t}")
                    except Exception as _e:
                        log_suppressed(logger, _e)
            except Exception:
                extracted.append(f"  {name} [解压失败]")

        summary = f"[ZIP: {fname}, {len(names)} 个文件]\n\n已解压到 uploads/:\n"
        summary += "\n".join(extracted[:30])
        if text_parts:
            summary += "\n\n--- 文本文件预览 ---\n" + "\n\n".join(text_parts[:5])
        return summary
    except Exception as e:
        return f"[ZIP 解析失败: {repr(e)}]"


def _analyze_image(content: bytes, fname: str) -> str:
    """Analyze image: vision model → OCR → fallback."""
    import base64
    b64 = base64.b64encode(content).decode()
    ext = fname.rsplit(".", 1)[-1].lower()
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
    mime = mime_map.get(ext, "image/png")

    # V86 Method 0: 专用视觉模型（HASHMM_VISION_* 配置后优先；未配置安静跳过）
    try:
        from hashmm.agent import vision as _vision
        if _vision.configured():
            desc, _err = _vision.describe_images([(b64, mime)])
            if desc:
                return f"[图片: {fname}，视觉模型分析结果]\n\n{desc}"
            if _err:
                logger.warning(f"Vision module analysis failed: {_err}")
    except Exception as e:  # 永不影响后续兜底
        logger.warning(f"Vision module error: {e}")

    # Method 1: Vision model
    model = db.get_default_model()
    if model and model.get("api_key"):
        try:
            from openai import OpenAI
            from hashmm.llm_timeout import client_timeout
            client = OpenAI(api_key=model["api_key"],
                            base_url=model.get("base_url", ""),
                            timeout=client_timeout())
            resp = client.chat.completions.create(
                model=model.get("model_name", ""),
                messages=[{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text",
                     "text": "请详细描述这张图片的内容，包括文字、图表、公式等。用中文回答。"},
                ]}],
                max_tokens=2000,
            )
            desc = resp.choices[0].message.content or ""
            if desc.strip():
                return f"[图片: {fname}，视觉模型分析结果]\n\n{desc}"
        except Exception as e:
            logger.warning(f"Vision analysis failed: {e}")

    # Method 2: OCR
    try:
        from PIL import Image as PILImage
        import pytesseract
        img = PILImage.open(io.BytesIO(content))
        ocr_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if ocr_text.strip() and len(ocr_text.strip()) > 10:
            return f"[图片: {fname}，OCR 识别结果]\n\n{ocr_text.strip()}"
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"OCR failed: {e}")

    return (
        f"[图片: {fname}, {len(content) // 1024}KB]\n"
        f"当前模型不支持图片分析，OCR 也未安装。\n"
        f"安装 OCR: apt-get install -y tesseract-ocr tesseract-ocr-chi-sim "
        f"&& pip install pytesseract Pillow --break-system-packages"
    )


# ═══════════════════════════════════════════════════════════════════
# v10.0: Document Preview API
# ═══════════════════════════════════════════════════════════════════

@router.get("/api/files/{filename}/metadata")
async def file_metadata(filename: str, request: Request):
    """Get document metadata (pages, outline, word count) without rendering."""
    filename = _auth_and_safe_name(request, filename)
    from hashmm.api.doc_preview import get_pptx_metadata, get_docx_metadata

    # Check both global and per-conv directories
    path = None
    for d in [Path("data/files"), Path("data/uploads")]:
        candidate = d / filename
        if candidate.exists():
            path = str(candidate)
            break

    if not path:
        raise HTTPException(404, "File not found")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pptx":
        return get_pptx_metadata(path)
    elif ext == "docx":
        return get_docx_metadata(path)
    elif ext in ("xlsx", "xls"):
        from hashmm.api.doc_preview import xlsx_to_json
        return xlsx_to_json(path)
    else:
        return {"error": f"Metadata extraction not supported for .{ext}"}


@router.get("/api/files/{filename}/thumbnail/{page}")
async def file_thumbnail(filename: str, request: Request, page: int = 1):
    """Get a rendered thumbnail for a specific page (PPTX/PDF)."""
    filename = _auth_and_safe_name(request, filename)
    from hashmm.api.doc_preview import render_pptx_thumbnails
    import tempfile

    path = None
    for d in [Path("data/files"), Path("data/uploads")]:
        candidate = d / filename
        if candidate.exists():
            path = str(candidate)
            break

    if not path:
        raise HTTPException(404, "File not found")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext != "pptx":
        raise HTTPException(400, "Thumbnails only supported for PPTX files")

    # Render thumbnails (cached per filename)
    cache_dir = Path(tempfile.gettempdir()) / "hashmm_thumbnails" / Path(filename).stem
    cache_dir.mkdir(parents=True, exist_ok=True)

    thumb_path = cache_dir / f"slide_{page:03d}.png"
    if not thumb_path.exists():
        pngs = render_pptx_thumbnails(path, str(cache_dir))
        if not pngs:
            raise HTTPException(503, "Thumbnail rendering unavailable (LibreOffice not installed)")

    if thumb_path.exists():
        return FileResponse(str(thumb_path), media_type="image/png")
    raise HTTPException(404, f"Page {page} not found")


@router.get("/api/files/{filename}/preview")
async def file_preview(filename: str, request: Request):
    """Get HTML preview of a document file (DOCX supported)."""
    filename = _auth_and_safe_name(request, filename)
    from fastapi.responses import HTMLResponse

    path = None
    for d in [Path("data/files"), Path("data/uploads")]:
        candidate = d / filename
        if candidate.exists():
            path = str(candidate)
            break

    if not path:
        raise HTTPException(404, "File not found")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "docx":
        from hashmm.api.doc_preview import docx_to_html
        html = docx_to_html(path)
        return HTMLResponse(content=html)
    elif ext in ("html", "htm"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return HTMLResponse(content=f.read())
    elif ext in ("txt", "md", "py", "json", "yaml", "yml", "csv"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return HTMLResponse(
            content=f'<pre style="font-family: monospace; font-size: 13px; padding: 20px; '
                    f'line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;">'
                    f'{escaped}</pre>'
        )
    else:
        raise HTTPException(400, f"Preview not supported for .{ext}")
