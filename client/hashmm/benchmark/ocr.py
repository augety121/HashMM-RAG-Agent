"""OCR helper for benchmark datasets that lack pre-extracted text.

ViDoRe v2 datasets often ship page images only. Standard text-retrieval
baselines (BGE-M3, BM25) need text. Tesseract is the canonical baseline
OCR engine — the ViDoRe v2 paper's "BGE-M3 (chunked OCR)" row uses it,
so we use the same.

Design:
  - Per-image disk cache: cache_dir / {hash}.txt
  - Parallel OCR via ThreadPoolExecutor (Tesseract releases the GIL).
  - Failures (corrupt image, bad page) yield empty string, never crash.
  - No GPU required.

Install:
  apt install tesseract-ocr tesseract-ocr-eng
  pip install pytesseract

Optional better engines (not used by default):
  - PaddleOCR: faster, GPU-accelerated, +600MB deps. Trade-off.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.ocr")


def _image_cache_key(image_path: Path, lang: str) -> str:
    """Stable cache key from absolute path + lang. Path content doesn't
    change in our use (images are dumped once and never edited), so the
    path string is enough."""
    h = hashlib.sha1(f"{image_path.resolve()}::{lang}".encode()).hexdigest()
    return h[:16]


def ocr_image(image_path: str | Path, lang: str = "eng",
              engine: str = "tesseract") -> str:
    """Run OCR on one image. Returns text or empty string on failure.

    Args:
        engine: 'tesseract' or 'paddleocr'. PaddleOCR is faster and more
                accurate on slide-heavy / low-quality scans, but needs
                `pip install paddlepaddle-gpu paddleocr`.
    """
    if engine == "paddleocr":
        return _ocr_paddleocr(image_path, lang)
    return _ocr_tesseract(image_path, lang)


# Lazy-initialised PaddleOCR singleton (heavy to create, reuse across calls)
_paddle_ocr_instance = None


def _get_paddle_ocr(lang: str = "en"):
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            raise ImportError(
                "pip install 'paddleocr<3' --break-system-packages"
            ) from e
        _paddle_ocr_instance = PaddleOCR(
            use_angle_cls=True, lang=lang, show_log=False, use_gpu=True,
        )
    return _paddle_ocr_instance


def _ocr_paddleocr(image_path: str | Path, lang: str = "eng") -> str:
    """PaddleOCR 2.x: better on slides than Tesseract."""
    paddle_lang = {"eng": "en", "fra": "fr", "chi_sim": "ch"}.get(lang, "en")
    try:
        ocr = _get_paddle_ocr(paddle_lang)
        result = ocr.ocr(str(image_path), cls=True)
        if not result or not result[0]:
            return ""
        lines = []
        for line_info in result[0]:
            if line_info and len(line_info) >= 2:
                text_block = line_info[1]
                if isinstance(text_block, (list, tuple)):
                    lines.append(str(text_block[0]))
                else:
                    lines.append(str(text_block))
        return "\n".join(lines).strip()
    except Exception as e:
        logger.warning("PaddleOCR failed for %s: %s", image_path, e)
        return ""


def _ocr_tesseract(image_path: str | Path, lang: str = "eng") -> str:
    """Original Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            "OCR needs `pip install pytesseract` plus the tesseract system "
            "binary (`apt install tesseract-ocr tesseract-ocr-eng`). "
            "Without these, datasets like ViDoRe v2 that ship without "
            "corpus_texts cannot be evaluated."
        ) from e

    try:
        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        text = pytesseract.image_to_string(img, lang=lang)
        return text.strip()
    except Exception as e:
        logger.warning("OCR failed for %s: %s", image_path, e)
        return ""


class OCRCache:
    """Per-image OCR result cache. Idempotent and thread-safe.

    Layout:
        cache_dir/
            <hash1>.txt
            <hash2>.txt
            ...
    """

    def __init__(self, cache_dir: str | Path, lang: str = "eng",
                 engine: str = "tesseract"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lang = lang
        self.engine = engine

    def _cache_path(self, image_path: Path) -> Path:
        key = _image_cache_key(image_path, self.lang)
        return self.cache_dir / f"{key}.txt"

    def get_or_ocr(self, image_path: str | Path) -> str:
        image_path = Path(image_path)
        cache_path = self._cache_path(image_path)
        if cache_path.exists():
            try:
                return cache_path.read_text(encoding="utf-8")
            except Exception:
                pass  # corrupt cache → re-OCR
        text = ocr_image(image_path, lang=self.lang, engine=self.engine)
        try:
            tmp = cache_path.with_suffix(".txt.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(cache_path)
        except Exception as e:
            logger.warning("OCR cache write failed: %s", e)
        return text

    def ocr_batch(self,
                  image_paths: Iterable[str | Path],
                  workers: int = 4,
                  progress_every: int = 50) -> dict[str, str]:
        """Parallel OCR with cache. Returns {str(path): text}.

        4 workers is a sweet spot for tesseract — it benefits from
        parallelism (releases GIL via the subprocess) but more workers
        thrash CPU. Override on big servers.
        """
        paths = [Path(p) for p in image_paths]
        results: dict[str, str] = {}
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self.get_or_ocr, p): p for p in paths}
            done = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    results[str(p)] = fut.result()
                except Exception as e:
                    logger.warning("OCR task crashed for %s: %s", p, e)
                    results[str(p)] = ""
                done += 1
                if done % progress_every == 0 or done == len(paths):
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (len(paths) - done) / rate if rate > 0 else 0
                    logger.info(
                        "  OCR %d / %d (%.1f / s, ETA %.0fs)",
                        done, len(paths), rate, eta,
                    )
        return results

    def stats(self) -> dict:
        files = list(self.cache_dir.glob("*.txt"))
        return {
            "cache_dir": str(self.cache_dir),
            "n_cached": len(files),
            "total_bytes": sum(f.stat().st_size for f in files),
        }
