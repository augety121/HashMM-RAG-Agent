"""Product OCR providers with typed failures and observable capabilities.

This module deliberately has no install side effects.  PaddleOCR is used only
when the already-provisioned runtime can import and initialize it.  Unlimited
OCR is an optional, isolated HTTP sidecar and is never imported into HashMM's
main Python environment.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class OCRProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = str(code or "ocr_error")[:80]
        self.retryable = bool(retryable)


_PADDLE_LOCK = threading.Lock()
_PADDLE_INSTANCES: dict[tuple[str, str], Any] = {}


def normalize_language(value: str, engine: str) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if engine == "paddleocr":
        if raw in {"chi_sim+eng", "chi_sim", "ch", "zh", "zh_cn", "zh+en"}:
            return "ch"
        if raw in {"eng", "en", "en_us"}:
            return "en"
        if raw in {"fra", "fr"}:
            return "fr"
        raise OCRProviderError("unsupported_language", f"PaddleOCR language is unsupported: {raw}")
    return raw or "chi_sim+eng"


def _paddle_instance(language: str, device: str = "gpu") -> Any:
    key = (normalize_language(language, "paddleocr"), str(device or "gpu"))
    with _PADDLE_LOCK:
        cached = _PADDLE_INSTANCES.get(key)
        if cached is not None:
            return cached
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRProviderError("provider_missing", "PaddleOCR is not installed") from exc
        try:
            instance = PaddleOCR(
                use_angle_cls=True,
                lang=key[0],
                show_log=False,
                use_gpu=key[1] == "gpu",
            )
        except Exception as exc:
            raise OCRProviderError(
                "provider_initialization_failed",
                f"PaddleOCR initialization failed: {type(exc).__name__}: {exc}",
                retryable=False,
            ) from exc
        _PADDLE_INSTANCES[key] = instance
        return instance


def _extract_paddle_result(result: Any, page: int) -> dict[str, Any]:
    rows = result[0] if isinstance(result, list) and result else []
    blocks: list[dict[str, Any]] = []
    texts: list[str] = []
    confidences: list[float] = []
    for position, row in enumerate(rows or []):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        payload = row[1]
        if isinstance(payload, (list, tuple)) and payload:
            text = str(payload[0] or "").strip()
            try:
                confidence = float(payload[1]) if len(payload) > 1 else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
        else:
            text, confidence = str(payload or "").strip(), 0.0
        if not text:
            continue
        bbox = row[0] if isinstance(row[0], (list, tuple)) else []
        blocks.append({
            "type": "text",
            "content": text,
            "page": max(1, int(page or 1)),
            "anchor": f"p.{max(1, int(page or 1))}",
            "position": position,
            "bbox": bbox,
            "confidence": max(0.0, min(confidence, 1.0)),
            "char_count": len(text),
            "is_noise": False,
        })
        texts.append(text)
        confidences.append(max(0.0, min(confidence, 1.0)))
    return {
        "text": "\n".join(texts),
        "blocks": blocks,
        "confidence": (sum(confidences) / len(confidences)) if confidences else 0.0,
    }


def recognize_image(
    image_path: str | Path,
    *,
    engine: str = "paddleocr",
    language: str = "chi_sim+eng",
    page: int = 1,
    device: str = "gpu",
) -> dict[str, Any]:
    path = Path(image_path).resolve()
    if not path.is_file():
        raise OCRProviderError("source_missing", "OCR image is missing")
    selected = str(engine or "paddleocr").strip().lower()
    started = time.time()
    if selected == "paddleocr":
        ocr = _paddle_instance(language, device=device)
        try:
            result = ocr.ocr(str(path), cls=True)
        except Exception as exc:
            raise OCRProviderError(
                "provider_runtime_failed",
                f"PaddleOCR inference failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc
        output = _extract_paddle_result(result, page)
    elif selected == "tesseract":
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise OCRProviderError("provider_missing", "Tesseract Python bindings are unavailable") from exc
        try:
            with Image.open(path) as image:
                text = pytesseract.image_to_string(
                    image.convert("RGB"), lang=normalize_language(language, "tesseract")
                ).strip()
        except Exception as exc:
            raise OCRProviderError(
                "provider_runtime_failed",
                f"Tesseract inference failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc
        output = {
            "text": text,
            "blocks": ([{
                "type": "text", "content": text, "page": max(1, int(page or 1)),
                "anchor": f"p.{max(1, int(page or 1))}", "position": 0,
                "bbox": [], "confidence": 0.0, "char_count": len(text),
                "is_noise": False,
            }] if text else []),
            "confidence": 0.0,
        }
    else:
        raise OCRProviderError("unknown_provider", f"Unknown OCR provider: {selected}")
    output.update({
        "schema": "hashmm.ocr-page.v2",
        "engine": selected,
        "language": normalize_language(language, selected),
        "page": max(1, int(page or 1)),
        "elapsed_ms": round((time.time() - started) * 1000),
    })
    return output


def _unlimited_health(timeout: float = 1.5) -> dict[str, Any]:
    base_url = str(os.environ.get("HASHMM_UNLIMITED_OCR_URL") or "").strip().rstrip("/")
    if not base_url:
        return {"enabled": False, "status": "disabled", "reason": "sidecar_url_not_configured"}
    try:
        request = urllib.request.Request(base_url + "/health", method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(64_000).decode("utf-8"))
        return {"enabled": True, "status": "ready", "endpoint": base_url, "detail": payload}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {
            "enabled": True,
            "status": "unavailable",
            "endpoint": base_url,
            "reason": f"{type(exc).__name__}: {exc}"[:240],
        }


def recognize_document_sidecar(
    document_path: str | Path,
    *,
    language: str = "chi_sim+eng",
    sha256: str = "",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Send bytes, never host paths or secrets, to an isolated OCR sidecar."""
    if str(os.environ.get("HASHMM_UNLIMITED_OCR_ENABLED") or "").lower() not in {"1", "true", "yes", "on"}:
        raise OCRProviderError("provider_disabled", "Unlimited OCR sidecar is disabled")
    base_url = str(os.environ.get("HASHMM_UNLIMITED_OCR_URL") or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    allow_remote = str(os.environ.get("HASHMM_UNLIMITED_OCR_ALLOW_REMOTE") or "").lower() in {"1", "true", "yes", "on"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OCRProviderError("invalid_sidecar_url", "Unlimited OCR sidecar URL is invalid")
    if not allow_remote and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise OCRProviderError("remote_sidecar_denied", "Unlimited OCR sidecar must be loopback-scoped")
    path = Path(document_path).resolve()
    if not path.is_file():
        raise OCRProviderError("source_missing", "OCR document is missing")
    maximum = max(1, min(int(os.environ.get("HASHMM_UNLIMITED_OCR_MAX_MB", "200") or 200), 1024)) * 1024 * 1024
    if path.stat().st_size > maximum:
        raise OCRProviderError("document_too_large", "Document exceeds the configured sidecar byte limit")
    request = urllib.request.Request(
        base_url + "/v1/parse",
        data=path.read_bytes(),
        headers={
            "Content-Type": "application/pdf",
            "X-HashMM-SHA256": str(sha256 or "")[:64],
            "X-HashMM-Language": str(language or "")[:40],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout or float(os.environ.get("HASHMM_UNLIMITED_OCR_TIMEOUT", "600") or 600),
        ) as response:
            payload = json.loads(response.read(64 * 1024 * 1024).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise OCRProviderError(
            "sidecar_request_failed", f"Unlimited OCR request failed: {type(exc).__name__}: {exc}",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise OCRProviderError("invalid_sidecar_response", "Unlimited OCR returned an invalid response")
    blocks: list[dict[str, Any]] = []
    for position, item in enumerate(payload["blocks"][:100_000]):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        page = max(1, int(item.get("page") or 1))
        blocks.append({
            "type": str(item.get("type") or "text")[:24],
            "content": content,
            "page": page,
            "anchor": f"p.{page}",
            "position": position,
            "bbox": item.get("bbox") if isinstance(item.get("bbox"), list) else [],
            "confidence": max(0.0, min(float(item.get("confidence") or 0), 1.0)),
            "char_count": len(content),
            "is_noise": False,
        })
    return {
        "schema": "hashmm.ocr-document.v2",
        "engine": "unlimited",
        "text": str(payload.get("text") or "")[:20_000_000],
        "blocks": blocks,
        "page_count": max(0, int(payload.get("page_count") or 0)),
        "failed_pages": [max(1, int(x)) for x in (payload.get("failed_pages") or [])[:10_000]],
        "blank_pages": [max(1, int(x)) for x in (payload.get("blank_pages") or [])[:10_000]],
        "model_revision": str(payload.get("model_revision") or "")[:160],
    }


def _paddle_canary() -> dict[str, Any]:
    """Initialize Paddle and run one real, local inference without networking."""
    try:
        from PIL import Image, ImageDraw
        with TemporaryDirectory(prefix="hashmm-ocr-canary-") as work:
            target = Path(work) / "canary.png"
            image = Image.new("RGB", (640, 180), "white")
            ImageDraw.Draw(image).text((40, 60), "HASHMM OCR 810", fill="black")
            image.save(target)
            result = recognize_image(
                target,
                engine="paddleocr",
                language="chi_sim+eng",
                device=os.environ.get("HASHMM_OCR_DEVICE", "gpu"),
            )
        if not str(result.get("text") or "").strip():
            return {"status": "unavailable", "detail": "canary_empty_output"}
        return {
            "status": "ready",
            "detail": "local_canary_passed",
            "elapsed_ms": int(result.get("elapsed_ms") or 0),
        }
    except Exception as exc:
        code = exc.code if isinstance(exc, OCRProviderError) else type(exc).__name__
        return {"status": "unavailable", "detail": f"{code}: {exc}"[:300]}


def capabilities(*, probe: bool = False) -> dict[str, Any]:
    paddle_installed = importlib.util.find_spec("paddleocr") is not None
    paddle_runtime = importlib.util.find_spec("paddle") is not None
    paddle_status = "installed_unverified" if paddle_installed and paddle_runtime else "unavailable"
    detail = ""
    if probe and paddle_installed and paddle_runtime:
        canary = _paddle_canary()
        paddle_status = str(canary.get("status") or "unavailable")
        detail = str(canary.get("detail") or "")
    return {
        "schema": "hashmm.ocr-capabilities.v2",
        "default_engine": str(os.environ.get("HASHMM_OCR_ENGINE") or "paddleocr"),
        "providers": {
            "paddleocr": {
                "installed": paddle_installed,
                "runtime_installed": paddle_runtime,
                "status": paddle_status,
                "detail": detail[:300],
                "languages": ["ch", "en", "fr"],
            },
            "tesseract": {
                "installed": importlib.util.find_spec("pytesseract") is not None,
                "status": "fallback_unverified",
            },
            "unlimited": _unlimited_health() if probe else {
                "enabled": bool(os.environ.get("HASHMM_UNLIMITED_OCR_URL")),
                "status": "configured_unverified" if os.environ.get("HASHMM_UNLIMITED_OCR_URL") else "disabled",
                "isolation": "http_sidecar",
            },
        },
    }


__all__ = [
    "OCRProviderError", "capabilities", "normalize_language", "recognize_document_sidecar",
    "recognize_image",
]
