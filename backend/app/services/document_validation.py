from __future__ import annotations

from collections.abc import Iterable

PDF_CONTENT_TYPE = "application/pdf"
PDF_SIGNATURE = b"%PDF-"
MAX_FILENAME_LENGTH = 255


class DocumentUploadError(Exception):
    status_code = 400
    detail = "Document upload is invalid."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail


class EmptyDocumentError(DocumentUploadError):
    status_code = 400
    detail = "The uploaded file is empty."


class InvalidFilenameError(DocumentUploadError):
    status_code = 400
    detail = "The uploaded filename is invalid."


class InvalidPdfSignatureError(DocumentUploadError):
    status_code = 400
    detail = "The uploaded file does not contain a valid PDF signature."


class UnsupportedDocumentTypeError(DocumentUploadError):
    status_code = 415
    detail = "Only PDF documents are supported."


class DocumentTooLargeError(DocumentUploadError):
    status_code = 413
    detail = "The uploaded file exceeds the maximum allowed size."


def normalize_content_type(content_type: str | None) -> str:
    if content_type is None:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def validate_declared_content_type(
    content_type: str | None,
    allowed_content_types: Iterable[str],
) -> str:
    normalized_content_type = normalize_content_type(content_type)
    normalized_allowed = {
        normalize_content_type(allowed) for allowed in allowed_content_types
    }
    if (
        not normalized_content_type
        or normalized_content_type not in normalized_allowed
    ):
        raise UnsupportedDocumentTypeError()
    return normalized_content_type


def sanitize_original_filename(filename: str | None) -> str:
    if filename is None:
        raise InvalidFilenameError()

    normalized = filename.replace("\x00", "").replace("\\", "/")
    candidate = normalized.rsplit("/", 1)[-1].strip()
    candidate = "".join(
        character for character in candidate if character.isprintable()
    )

    if not candidate or candidate in {".", ".."}:
        raise InvalidFilenameError()
    if len(candidate) > MAX_FILENAME_LENGTH:
        raise InvalidFilenameError(
            f"Filename must be {MAX_FILENAME_LENGTH} characters or fewer."
        )
    return candidate


def validate_pdf_signature(header: bytes) -> None:
    if not header.startswith(PDF_SIGNATURE):
        raise InvalidPdfSignatureError()
