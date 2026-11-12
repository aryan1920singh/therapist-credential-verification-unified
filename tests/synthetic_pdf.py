from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.services.pdf_extraction import PDFTextExtractor

TESSDATA_PATH = Path(settings.tesseract_data_dir)
TESSDATA_AVAILABLE = (TESSDATA_PATH / "eng.traineddata").is_file()

requires_tessdata = pytest.mark.skipif(
    not TESSDATA_AVAILABLE,
    reason=(
        "English Tesseract language data "
        "(storage/tessdata/eng.traineddata) is not available"
    ),
)

SYNTHETIC_ENCRYPTION_PASSWORD = "Synthetic-Page-Password-91"


def build_extractor(**overrides: object) -> PDFTextExtractor:
    options: dict[str, object] = {
        "max_pages": 30,
        "max_text_bytes": 2 * 1024 * 1024,
        "render_dpi": 200,
        "ocr_min_chars": 50,
        "ocr_page_timeout_seconds": 30.0,
        "ocr_total_timeout_seconds": 300.0,
        "tesseract_data_dir": TESSDATA_PATH,
    }
    options.update(overrides)
    return PDFTextExtractor(**options)


def load_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_text_image(
    text: str,
    size: tuple[int, int] = (1200, 400),
) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = load_font(72)
    draw.text(
        (60, size[1] // 2 - 45),
        text,
        font=font,
        fill="black",
    )
    return image


def _image_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def embedded_pdf_bytes(page_texts: list[str]) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page(width=300, height=120)
        page.insert_text((30, 60), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def scanned_pdf_bytes(page_texts: list[str]) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page(width=400, height=133)
        page.insert_image(
            page.rect,
            stream=_image_bytes(make_text_image(text)),
        )
    payload = document.tobytes()
    document.close()
    return payload


def mixed_pdf_bytes() -> bytes:
    document = pymupdf.open()
    digital = document.new_page(width=300, height=120)
    digital.insert_text((30, 60), "MIXED DIGITAL PAGE ONE", fontsize=12)
    scanned = document.new_page(width=400, height=133)
    scanned.insert_image(
        scanned.rect,
        stream=_image_bytes(make_text_image("MIXED SCANNED PAGE TWO")),
    )
    payload = document.tobytes()
    document.close()
    return payload


def blank_image_pdf_bytes() -> bytes:
    document = pymupdf.open()
    digital = document.new_page(width=300, height=120)
    digital.insert_text((30, 60), "DIGITAL PAGE WITH TEXT", fontsize=12)
    scanned = document.new_page(width=400, height=133)
    scanned.insert_image(
        scanned.rect,
        stream=_image_bytes(Image.new("RGB", (400, 133), "white")),
    )
    payload = document.tobytes()
    document.close()
    return payload


def blank_pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page(width=300, height=120)
    payload = document.tobytes()
    document.close()
    return payload


def text_and_blank_pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=120)
    page.insert_text((30, 60), "READABLE PAGE TEXT", fontsize=12)
    document.new_page(width=300, height=120)
    payload = document.tobytes()
    document.close()
    return payload


def encrypted_pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page(width=300, height=120)
    buffer = io.BytesIO()
    document.save(
        buffer,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=SYNTHETIC_ENCRYPTION_PASSWORD,
        owner_pw=SYNTHETIC_ENCRYPTION_PASSWORD,
    )
    payload = buffer.getvalue()
    document.close()
    return payload


def multi_line_pdf_bytes(line_count: int = 80) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=1200)
    for index in range(line_count):
        line = (
            f"Line {index:03d} alpha beta gamma delta "
            "epsilon zeta eta theta"
        )
        page.insert_text((10, 20 + index * 13), line, fontsize=8)
    payload = document.tobytes()
    document.close()
    return payload


def write_pdf(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path
