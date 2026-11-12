from __future__ import annotations

import io
import shutil
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf
import pytesseract
from PIL import Image

from app.core.config import settings

EMBEDDED_TEXT_METHOD = "embedded_text"
OCR_METHOD = "ocr"
BLANK_METHOD = "blank"

MIN_OCR_WORD_CONFIDENCE = 0.0
MAX_OCR_WORD_CONFIDENCE = 100.0
OCR_CONFIDENCE_SCALE = 100.0
OCR_CONFIDENCE_DECIMALS = 4


def aggregate_ocr_confidence(
    word_confidences: Iterable[object],
) -> float | None:
    """Aggregate word-level Tesseract confidences into a 0.0-1.0 mean.

    Tesseract reports per-word confidence as a percentage (0-100). Values
    outside that range (for example -1 for invalid entries) are discarded.
    The arithmetic mean of the remaining values is divided by 100 and
    rounded to four decimal places. Returns ``None`` when no valid word
    confidence is available. This measures OCR recognition confidence
    only; it says nothing about document authenticity or validity.
    """
    valid: list[float] = []
    for value in word_confidences:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if MIN_OCR_WORD_CONFIDENCE <= number <= MAX_OCR_WORD_CONFIDENCE:
            valid.append(number)
    if not valid:
        return None
    mean_percent = sum(valid) / len(valid)
    return round(mean_percent / OCR_CONFIDENCE_SCALE, OCR_CONFIDENCE_DECIMALS)


class PDFExtractionError(Exception):
    code = "extraction_failed"
    status_code = 500
    detail = "The PDF could not be extracted."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail


class SourceDocumentMissingError(PDFExtractionError):
    code = "source_document_missing"
    status_code = 409
    detail = "The stored document file is unavailable."


class DocumentAlreadyExtractedError(PDFExtractionError):
    code = "document_already_extracted"
    status_code = 409
    detail = "This document has already been extracted."


class EncryptedPDFError(PDFExtractionError):
    code = "encrypted_pdf"
    status_code = 422
    detail = "Encrypted PDFs cannot be processed."


class CorruptedPDFError(PDFExtractionError):
    code = "corrupted_pdf"
    status_code = 422
    detail = "The PDF could not be opened or parsed."


class EmptyPDFError(PDFExtractionError):
    code = "empty_pdf"
    status_code = 422
    detail = "The PDF contains no pages."


class NoExtractableTextError(PDFExtractionError):
    code = "no_extractable_text"
    status_code = 422
    detail = "The PDF contains no extractable text."


class PageLimitExceededError(PDFExtractionError):
    code = "page_limit_exceeded"
    status_code = 413
    detail = "The PDF exceeds the maximum allowed page count."


class TextLimitExceededError(PDFExtractionError):
    code = "text_limit_exceeded"
    status_code = 413
    detail = "The PDF exceeds the maximum allowed extracted text size."


class OCRUnavailableError(PDFExtractionError):
    code = "ocr_unavailable"
    status_code = 503
    detail = "OCR is required but the OCR engine is unavailable."


class OCRTimeoutError(PDFExtractionError):
    code = "ocr_timeout"
    status_code = 504
    detail = "OCR exceeded the allowed processing time."


class OCRProcessingError(PDFExtractionError):
    code = "ocr_failed"
    status_code = 500
    detail = "OCR processing failed."


class ExtractionStorageError(PDFExtractionError):
    code = "extraction_storage_failed"
    status_code = 500
    detail = "The extracted text could not be stored."


class OCRBackend(Protocol):
    def extract_text(self, image: Image.Image) -> str:
        ...

    def extract_text_with_confidence(
        self, image: Image.Image
    ) -> tuple[str, float | None]:
        ...


@dataclass(frozen=True)
class PageExtraction:
    page_number: int
    text: str
    extraction_method: str
    ocr_confidence: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "extraction_method": self.extraction_method,
            "ocr_confidence": self.ocr_confidence,
        }


@dataclass(frozen=True)
class PDFExtractionResult:
    page_count: int
    pages: list[PageExtraction]
    text_bytes: int
    ocr_page_count: int


class TesseractOCRBackend:
    def __init__(
        self,
        *,
        command: str | None = None,
        data_dir: str | Path | None = None,
        language: str = "eng",
        page_timeout_seconds: float = 30.0,
    ) -> None:
        self.command = command
        self.data_dir = (
            Path(data_dir).expanduser().resolve()
            if data_dir is not None
            else None
        )
        self.language = language
        self.page_timeout_seconds = page_timeout_seconds

    def extract_text(self, image: Image.Image) -> str:
        text, _ = self.extract_text_with_confidence(image)
        return text

    def extract_text_with_confidence(
        self, image: Image.Image
    ) -> tuple[str, float | None]:
        executable = self._resolve_executable()
        if executable is not None:
            return self._extract_with_cli(image, executable)
        return self._extract_with_tesserocr(image)

    def version(self) -> str:
        executable = self._resolve_executable()
        if executable is not None:
            try:
                version = str(pytesseract.get_tesseract_version())
            except Exception:
                return "unknown"
        else:
            try:
                import tesserocr

                version = str(tesserocr.tesseract_version())
            except Exception:
                return "unknown"

        first_line = version.splitlines()[0].strip()
        if first_line.lower().startswith("tesseract "):
            first_line = first_line[10:].strip()
        return first_line or "unknown"

    def _resolve_executable(self) -> str | None:
        if self.command:
            executable = shutil.which(self.command)
            if executable:
                return executable
        return shutil.which("tesseract")

    def _extract_with_cli(
        self,
        image: Image.Image,
        executable: str,
    ) -> tuple[str, float | None]:
        pytesseract.pytesseract.tesseract_cmd = executable
        try:
            text = pytesseract.image_to_string(
                image,
                lang=self.language,
                config="--psm 3",
                timeout=self.page_timeout_seconds,
            )
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config="--psm 3",
                timeout=self.page_timeout_seconds,
                output_type=pytesseract.Output.DICT,
            )
        except RuntimeError as exc:
            if "timeout" in str(exc).lower():
                raise OCRTimeoutError() from exc
            raise OCRProcessingError() from exc
        except pytesseract.TesseractNotFoundError as exc:
            raise OCRUnavailableError() from exc
        except Exception as exc:
            raise OCRProcessingError() from exc
        confidences = data.get("conf", []) if isinstance(data, dict) else []
        return text, aggregate_ocr_confidence(confidences)

    def _extract_with_tesserocr(
        self,
        image: Image.Image,
    ) -> tuple[str, float | None]:
        self._validate_tesserocr_data()

        results: list[tuple[str, float | None]] = []
        errors: list[BaseException] = []

        def _recognize() -> None:
            try:
                results.append(self._recognize_tesserocr(image))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(
            target=_recognize,
            name="tesseract-ocr-worker",
            daemon=True,
        )
        worker.start()
        worker.join(self.page_timeout_seconds)
        if worker.is_alive():
            raise OCRTimeoutError()

        if errors:
            error = errors[0]
            if isinstance(error, PDFExtractionError):
                raise error
            raise OCRProcessingError() from error
        if not results:
            raise OCRProcessingError()
        return results[0]

    def _recognize_tesserocr(
        self,
        image: Image.Image,
    ) -> tuple[str, float | None]:
        try:
            from tesserocr import PSM, PyTessBaseAPI
        except ImportError as exc:
            raise OCRUnavailableError() from exc

        try:
            with PyTessBaseAPI(
                path=str(self.data_dir),
                lang=self.language,
                psm=PSM.AUTO,
            ) as api:
                api.SetImage(image)
                api.Recognize()
                text = api.GetUTF8Text() or ""
                confidences = api.AllWordConfidences()
        except PDFExtractionError:
            raise
        except (OSError, RuntimeError) as exc:
            raise OCRProcessingError() from exc
        except Exception as exc:
            raise OCRProcessingError() from exc
        return text, aggregate_ocr_confidence(confidences)

    def _validate_tesserocr_data(self) -> None:
        if self.data_dir is None:
            raise OCRUnavailableError()
        language_file = self.data_dir / f"{self.language}.traineddata"
        if not language_file.is_file():
            raise OCRUnavailableError()


class PDFTextExtractor:
    model_name = "pymupdf+tesseract"

    def __init__(
        self,
        *,
        max_pages: int,
        max_text_bytes: int,
        render_dpi: int,
        ocr_min_chars: int,
        ocr_page_timeout_seconds: float,
        ocr_total_timeout_seconds: float,
        tesseract_command: str | None = None,
        tesseract_data_dir: str | Path | None = None,
        tesseract_language: str = "eng",
        ocr_backend: OCRBackend | None = None,
    ) -> None:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if max_text_bytes <= 0:
            raise ValueError("max_text_bytes must be positive")
        if render_dpi <= 0:
            raise ValueError("render_dpi must be positive")
        if ocr_min_chars < 0:
            raise ValueError("ocr_min_chars must be non-negative")
        if ocr_page_timeout_seconds <= 0:
            raise ValueError("ocr_page_timeout_seconds must be positive")
        if ocr_total_timeout_seconds <= 0:
            raise ValueError("ocr_total_timeout_seconds must be positive")

        self.max_pages = max_pages
        self.max_text_bytes = max_text_bytes
        self.render_dpi = render_dpi
        self.ocr_min_chars = ocr_min_chars
        self.ocr_total_timeout_seconds = ocr_total_timeout_seconds
        self.ocr_backend = ocr_backend or TesseractOCRBackend(
            command=tesseract_command,
            data_dir=tesseract_data_dir,
            language=tesseract_language,
            page_timeout_seconds=ocr_page_timeout_seconds,
        )

    @property
    def model_version(self) -> str:
        tesseract_version = getattr(self.ocr_backend, "version", None)
        if callable(tesseract_version):
            version = tesseract_version()
        else:
            version = "unknown"
        return f"pymupdf-{pymupdf.__version__};tesseract-{version}"[
            :100
        ]

    def extract_file(self, path: str | Path) -> PDFExtractionResult:
        try:
            document = pymupdf.open(path)
        except Exception as exc:
            raise CorruptedPDFError() from exc

        try:
            if getattr(document, "needs_pass", False) or getattr(
                document,
                "is_encrypted",
                False,
            ):
                raise EncryptedPDFError()

            page_count = document.page_count
            if page_count <= 0:
                raise EmptyPDFError()
            if page_count > self.max_pages:
                raise PageLimitExceededError()

            pages: list[PageExtraction] = []
            text_bytes = 0
            ocr_page_count = 0
            started_at = time.monotonic()

            for page_index in range(page_count):
                page = document.load_page(page_index)
                text = page.get_text("text") or ""
                has_images = bool(page.get_images(full=True))
                non_whitespace_chars = sum(
                    1 for character in text if not character.isspace()
                )
                page_ocr_confidence: float | None = None

                if (
                    non_whitespace_chars >= self.ocr_min_chars
                    or not has_images
                ):
                    extraction_method = (
                        EMBEDDED_TEXT_METHOD if text.strip() else BLANK_METHOD
                    )
                else:
                    if (
                        time.monotonic() - started_at
                        >= self.ocr_total_timeout_seconds
                    ):
                        raise OCRTimeoutError()
                    text, page_ocr_confidence = self._ocr_page(page)
                    extraction_method = OCR_METHOD
                    ocr_page_count += 1
                    if (
                        time.monotonic() - started_at
                        >= self.ocr_total_timeout_seconds
                    ):
                        raise OCRTimeoutError()

                text_bytes += len(text.encode("utf-8", errors="replace"))
                if text_bytes > self.max_text_bytes:
                    raise TextLimitExceededError()

                pages.append(
                    PageExtraction(
                        page_number=page_index + 1,
                        text=text,
                        extraction_method=extraction_method,
                        ocr_confidence=page_ocr_confidence,
                    )
                )

            if not any(page.text.strip() for page in pages):
                raise NoExtractableTextError()

            return PDFExtractionResult(
                page_count=page_count,
                pages=pages,
                text_bytes=text_bytes,
                ocr_page_count=ocr_page_count,
            )
        except PDFExtractionError:
            raise
        except Exception as exc:
            raise PDFExtractionError() from exc
        finally:
            document.close()

    def _ocr_page(self, page: pymupdf.Page) -> tuple[str, float | None]:
        try:
            pixmap = page.get_pixmap(
                dpi=self.render_dpi,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            image = Image.open(
                io.BytesIO(pixmap.tobytes("png"))
            ).convert("RGB")
        except Exception as exc:
            raise OCRProcessingError() from exc

        confidence_method = getattr(
            self.ocr_backend, "extract_text_with_confidence", None
        )
        try:
            if callable(confidence_method):
                return confidence_method(image)
            return self.ocr_backend.extract_text(image), None
        except PDFExtractionError:
            raise
        except Exception as exc:
            raise OCRProcessingError() from exc


def get_pdf_text_extractor() -> PDFTextExtractor:
    return PDFTextExtractor(
        max_pages=settings.pdf_extraction_max_pages,
        max_text_bytes=settings.pdf_extraction_max_text_bytes,
        render_dpi=settings.pdf_extraction_render_dpi,
        ocr_min_chars=settings.pdf_extraction_ocr_min_chars,
        ocr_page_timeout_seconds=(
            settings.pdf_extraction_ocr_page_timeout_seconds
        ),
        ocr_total_timeout_seconds=(
            settings.pdf_extraction_ocr_total_timeout_seconds
        ),
        tesseract_command=settings.tesseract_cmd,
        tesseract_data_dir=settings.tesseract_data_dir,
        tesseract_language=settings.tesseract_language,
    )
