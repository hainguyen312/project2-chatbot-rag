"""
PDF → chữ.

- Bỏ chú thích cuối trang và số chú thích (chữ nhỏ hơn hẳn cỡ chữ nội dung), tránh lỗi
  "Điều 5" + chú thích "1" thành "Điều 51" trong văn bản hợp nhất.
- Trang gần như không có chữ (bản scan) → OCR local nếu đã cài PaddleOCR, nếu không thì
  ghi cảnh báo để người duyệt biết (không gọi LLM để tiết kiệm token).
"""
from __future__ import annotations

import collections
import io
import unicodedata
from dataclasses import dataclass, field

MIN_CHARS_PER_PAGE = 40      # ít hơn → coi là trang scan
FOOTNOTE_SIZE_RATIO = 0.8    # chữ nhỏ hơn 80% cỡ chữ nội dung → chú thích


@dataclass
class ExtractResult:
    text: str
    pages: int
    scanned_pages: list[int] = field(default_factory=list)   # 1-based
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _body_font_size(pdf) -> float:
    cnt: collections.Counter = collections.Counter()
    for p in pdf.pages[:10]:
        for c in p.chars:
            if c.get("text", "").strip():
                cnt[round(c["size"], 1)] += 1
    return cnt.most_common(1)[0][0] if cnt else 0.0


def _ocr_page(page) -> str | None:
    """OCR một trang bằng PaddleOCR (local). None nếu chưa cài."""
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return None
    global _OCR
    try:
        _OCR
    except NameError:
        _OCR = PaddleOCR(lang="vi", use_angle_cls=True, show_log=False)  # noqa: F841
    import numpy as np  # paddle cần numpy

    img = page.to_image(resolution=300).original
    result = _OCR.ocr(np.array(img), cls=True)  # type: ignore[name-defined]
    lines = [ln[1][0] for block in (result or []) for ln in (block or [])]
    return "\n".join(lines)


def extract_pdf(data: bytes) -> ExtractResult:
    import pdfplumber

    out: list[str] = []
    res = ExtractResult(text="", pages=0)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        res.pages = len(pdf.pages)
        body = _body_font_size(pdf)
        threshold = body * FOOTNOTE_SIZE_RATIO if body else 0
        for i, page in enumerate(pdf.pages, start=1):
            if threshold:
                page = page.filter(lambda o: o.get("object_type") != "char" or o.get("size", 0) >= threshold)
            text = page.extract_text() or ""
            if len(text.strip()) < MIN_CHARS_PER_PAGE and (page.images or i > 1):
                res.scanned_pages.append(i)
                ocr = _ocr_page(page)
                if ocr:
                    res.ocr_pages.append(i)
                    text = ocr
            out.append(text)
    if res.scanned_pages and not res.ocr_pages:
        res.warnings.append(
            f"{len(res.scanned_pages)} trang không có lớp chữ (bản scan): {res.scanned_pages[:10]}. "
            "Cài PaddleOCR để OCR local, hoặc tìm bản PDF có lớp chữ."
        )
    elif res.ocr_pages:
        res.warnings.append(f"{len(res.ocr_pages)} trang được OCR — cần đối chiếu kỹ chính tả/dấu.")
    res.text = unicodedata.normalize("NFC", "\n".join(out)).replace("\xa0", " ")
    return res
