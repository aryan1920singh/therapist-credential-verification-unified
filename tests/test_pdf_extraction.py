from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
import pytesseract
from PIL import Image

from app.services.pdf_extraction import (
    BLANK_METHOD,
    EMBEDDED_TEXT_METHOD,
    OCR_METHOD,
    CorruptedPDFError,
    EncryptedPDFError,
    NoExtractableTextError,
    OCRProcessingError,
    OCRTimeoutError,
    OCRUnavailableError,
    PageLimitExceededError,
    PDFTextExtractor,
    TesseractOCRBackend,
    TextLimitExceededError,
    aggregate_ocr_confidence,
)
from tests.synthetic_pdf import (
    TESSDATA_PATH,
    blank_image_pdf_bytes,
    blank_pdf_bytes,
    build_extractor,
    embedded_pdf_bytes,
    encrypted_pdf_bytes,
    mixed_pdf_bytes,
    requires_tessdata,
    scanned_pdf_bytes,
    text_and_blank_pdf_bytes,
    write_pdf,
)

CORRUPTED_PDF_BYTES = b"%PDF-1.4\nthis is not a valid pdf body"


def test_embedded_text_pdf_extracts_text_with_1_based_pages(
    tmp_path: Path,
) -> None:
    pdf_path = write_pdf(
        tmp_path / "embedded.pdf",
        embedded_pdf_bytes(["FIRST PAGE TEXT", "SECOND PAGE TEXT"]),
    )

    result = build_extractor().extract_file(pdf_path)

    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert [page.extraction_method for page in result.pages] == [
        EMBEDDED_TEXT_METHOD,
        EMBEDDED_TEXT_METHOD,
    ]
    assert "FIRST PAGE TEXT" in result.pages[0].text
    assert "SECOND PAGE TEXT" in result.pages[1].text
    assert result.ocr_page_count == 0
    assert result.text_bytes == sum(
        len(page.text.encode("utf-8")) for page in result.pages
    )
    assert all(page.ocr_confidence is None for page in result.pages)


@requires_tessdata
def test_scanned_pdf_page_is_extracted_with_ocr(tmp_path: Path) -> None:
    pdf_path = write_pdf(
        tmp_path / "scanned.pdf",
        scanned_pdf_bytes(["CREDENTIAL TEST"]),
    )

    result = build_extractor().extract_file(pdf_path)

    assert result.page_count == 1
    assert result.ocr_page_count == 1
    page = result.pages[0]
    assert page.page_number == 1
    assert page.extraction_method == OCR_METHOD
    normalized = "".join(
        character for character in page.text.upper() if character.isalnum()
    )
    assert "CREDENTIAL" in normalized
    assert page.ocr_confidence is not None
    assert 0.0 < page.ocr_confidence <= 1.0


@requires_tessdata
def test_mixed_pdf_uses_expected_method_per_page(tmp_path: Path) -> None:
    pdf_path = write_pdf(tmp_path / "mixed.pdf", mixed_pdf_bytes())

    result = build_extractor().extract_file(pdf_path)

    assert result.page_count == 2
    assert result.ocr_page_count == 1
    assert [page.page_number for page in result.pages] == [1, 2]
    assert [page.extraction_method for page in result.pages] == [
        EMBEDDED_TEXT_METHOD,
        OCR_METHOD,
    ]
    assert "MIXED DIGITAL PAGE ONE" in result.pages[0].text
    normalized = "".join(
        character
        for character in result.pages[1].text.upper()
        if character.isalnum()
    )
    assert "SCANNED" in normalized
    assert result.pages[0].ocr_confidence is None
    assert result.pages[1].ocr_confidence is not None


def test_blank_pdf_reports_no_extractable_text(tmp_path: Path) -> None:
    pdf_path = write_pdf(tmp_path / "blank.pdf", blank_pdf_bytes())

    with pytest.raises(NoExtractableTextError) as excinfo:
        build_extractor().extract_file(pdf_path)

    assert excinfo.value.code == "no_extractable_text"
    assert excinfo.value.status_code == 422


def test_blank_page_alongside_text_page_is_recorded(
    tmp_path: Path,
) -> None:
    pdf_path = write_pdf(
        tmp_path / "text-and-blank.pdf",
        text_and_blank_pdf_bytes(),
    )

    result = build_extractor().extract_file(pdf_path)

    assert result.page_count == 2
    assert result.pages[0].extraction_method == EMBEDDED_TEXT_METHOD
    assert result.pages[1].extraction_method == BLANK_METHOD
    assert result.pages[1].text.strip() == ""
    assert result.pages[1].ocr_confidence is None


def test_corrupted_pdf_raises_domain_error(tmp_path: Path) -> None:
    pdf_path = write_pdf(tmp_path / "corrupted.pdf", CORRUPTED_PDF_BYTES)

    with pytest.raises(CorruptedPDFError) as excinfo:
        build_extractor().extract_file(pdf_path)

    assert excinfo.value.code == "corrupted_pdf"
    assert excinfo.value.status_code == 422


def test_encrypted_pdf_raises_domain_error(tmp_path: Path) -> None:
    pdf_path = write_pdf(tmp_path / "encrypted.pdf", encrypted_pdf_bytes())

    with pytest.raises(EncryptedPDFError) as excinfo:
        build_extractor().extract_file(pdf_path)

    assert excinfo.value.code == "encrypted_pdf"
    assert excinfo.value.status_code == 422


def test_page_limit_is_enforced(tmp_path: Path) -> None:
    pdf_path = write_pdf(
        tmp_path / "two-pages.pdf",
        embedded_pdf_bytes(["PAGE ONE", "PAGE TWO"]),
    )

    with pytest.raises(PageLimitExceededError) as excinfo:
        build_extractor(max_pages=1).extract_file(pdf_path)

    assert excinfo.value.code == "page_limit_exceeded"
    assert excinfo.value.status_code == 413


def test_extracted_text_size_limit_is_enforced(tmp_path: Path) -> None:
    pdf_path = write_pdf(
        tmp_path / "long-text.pdf",
        embedded_pdf_bytes(["TEXT LIMIT TEST PAYLOAD"]),
    )

    with pytest.raises(TextLimitExceededError) as excinfo:
        build_extractor(max_text_bytes=8).extract_file(pdf_path)

    assert excinfo.value.code == "text_limit_exceeded"
    assert excinfo.value.status_code == 413


def test_ocr_unavailable_without_language_data(tmp_path: Path) -> None:
    pdf_path = write_pdf(
        tmp_path / "scanned.pdf",
        scanned_pdf_bytes(["CREDENTIAL TEST"]),
    )
    extractor = build_extractor(
        ocr_backend=TesseractOCRBackend(
            data_dir=tmp_path / "missing-tessdata",
        ),
    )

    with pytest.raises(OCRUnavailableError) as excinfo:
        extractor.extract_file(pdf_path)

    assert excinfo.value.code == "ocr_unavailable"
    assert excinfo.value.status_code == 503


@requires_tessdata
def test_ocr_timeout_is_enforced_per_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_recognize(self, image):
        time.sleep(1.0)
        return "", None

    monkeypatch.setattr(
        TesseractOCRBackend, "_recognize_tesserocr", slow_recognize
    )
    pdf_path = write_pdf(
        tmp_path / "scanned.pdf",
        scanned_pdf_bytes(["CREDENTIAL TEST"]),
    )
    extractor = build_extractor(
        ocr_backend=TesseractOCRBackend(
            data_dir=TESSDATA_PATH,
            page_timeout_seconds=0.05,
        ),
    )

    with pytest.raises(OCRTimeoutError) as excinfo:
        extractor.extract_file(pdf_path)

    assert excinfo.value.code == "ocr_timeout"
    assert excinfo.value.status_code == 504


@requires_tessdata
def test_ocr_processing_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_recognize(self, image):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(
        TesseractOCRBackend, "_recognize_tesserocr", broken_recognize
    )
    pdf_path = write_pdf(
        tmp_path / "scanned.pdf",
        scanned_pdf_bytes(["CREDENTIAL TEST"]),
    )
    extractor = build_extractor(
        ocr_backend=TesseractOCRBackend(data_dir=TESSDATA_PATH),
    )

    with pytest.raises(OCRProcessingError) as excinfo:
        extractor.extract_file(pdf_path)

    assert excinfo.value.code == "ocr_failed"
    assert excinfo.value.status_code == 500


@requires_tessdata
def test_ocr_page_without_words_reports_null_confidence(
    tmp_path: Path,
) -> None:
    pdf_path = write_pdf(
        tmp_path / "text-and-blank-image.pdf",
        blank_image_pdf_bytes(),
    )

    result = build_extractor().extract_file(pdf_path)

    assert result.page_count == 2
    assert result.ocr_page_count == 1
    ocr_page = result.pages[1]
    assert ocr_page.extraction_method == OCR_METHOD
    assert ocr_page.text.strip() == ""
    assert ocr_page.ocr_confidence is None
    assert result.pages[0].ocr_confidence is None


def test_aggregate_ocr_confidence_averages_valid_values() -> None:
    assert aggregate_ocr_confidence([95, 96, -1]) == 0.955
    assert aggregate_ocr_confidence([100]) == 1.0
    assert aggregate_ocr_confidence([0, 100]) == 0.5


def test_aggregate_ocr_confidence_returns_none_without_valid_values() -> None:
    assert aggregate_ocr_confidence([]) is None
    assert aggregate_ocr_confidence([-1, 101, None, "not-a-number"]) is None


def test_cli_backend_reports_aggregated_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = TesseractOCRBackend(page_timeout_seconds=5.0)
    monkeypatch.setattr(
        backend, "_resolve_executable", lambda: "C:/fake/tesseract.exe"
    )
    monkeypatch.setattr(
        pytesseract.pytesseract,
        "tesseract_cmd",
        "C:/fake/tesseract.exe",
    )
    monkeypatch.setattr(
        pytesseract,
        "image_to_string",
        lambda *args, **kwargs: "CLI TEXT",
    )
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {"conf": ["90", "80", "-1"]},
    )

    text, confidence = backend.extract_text_with_confidence(
        Image.new("RGB", (10, 10), "white")
    )

    assert text == "CLI TEXT"
    assert confidence == 0.85


def test_extract_file_leaves_source_unchanged(tmp_path: Path) -> None:
    payload = embedded_pdf_bytes(["SOURCE INTEGRITY CHECK"])
    pdf_path = write_pdf(tmp_path / "source.pdf", payload)
    digest_before = hashlib.sha256(payload).hexdigest()

    build_extractor().extract_file(pdf_path)

    assert pdf_path.read_bytes() == payload
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == (
        digest_before
    )


def test_extractor_reports_model_name_and_version() -> None:
    extractor: PDFTextExtractor = build_extractor()

    assert extractor.model_name == "pymupdf+tesseract"
    assert extractor.model_version.startswith("pymupdf-")
    assert "tesseract-" in extractor.model_version
