from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from app.core.config import settings
from app.services.document_validation import (
    DocumentUploadError,
    DocumentTooLargeError,
    EmptyDocumentError,
    PDF_CONTENT_TYPE,
    sanitize_original_filename,
    validate_declared_content_type,
    validate_pdf_signature,
)

DEFAULT_CHUNK_SIZE_BYTES = 1024 * 1024


class DocumentStorageError(Exception):
    status_code = 500
    detail = "The document could not be stored."


class InvalidStorageKeyError(ValueError):
    pass


@dataclass(frozen=True)
class StoredDocument:
    storage_key: str
    original_filename: str
    content_type: str
    file_size_bytes: int
    sha256_checksum: str


class PrivateDocumentStorage:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        max_upload_size_bytes: int,
        allowed_content_types: Iterable[str] = (PDF_CONTENT_TYPE,),
        chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    ) -> None:
        if max_upload_size_bytes <= 0:
            raise ValueError("max_upload_size_bytes must be positive")
        if chunk_size_bytes <= 0:
            raise ValueError("chunk_size_bytes must be positive")

        self.root_dir = Path(root_dir).expanduser().resolve()
        self.max_upload_size_bytes = max_upload_size_bytes
        self.allowed_content_types = tuple(allowed_content_types)
        self.chunk_size_bytes = chunk_size_bytes

    def save_pdf(
        self,
        stream: BinaryIO,
        *,
        declared_content_type: str | None,
        original_filename: str | None,
    ) -> StoredDocument:
        content_type = validate_declared_content_type(
            declared_content_type,
            self.allowed_content_types,
        )
        safe_filename = sanitize_original_filename(original_filename)

        first_chunk = stream.read(self.chunk_size_bytes)
        if not first_chunk:
            raise EmptyDocumentError()

        validate_pdf_signature(first_chunk)
        if len(first_chunk) > self.max_upload_size_bytes:
            raise DocumentTooLargeError()

        self._ensure_root()
        storage_key, destination, destination_handle = (
            self._create_exclusive_destination()
        )
        hasher = hashlib.sha256()
        total_size = 0

        try:
            with destination_handle:
                total_size = self._write_chunk(
                    destination_handle,
                    hasher,
                    first_chunk,
                    total_size,
                )

                while True:
                    chunk = stream.read(self.chunk_size_bytes)
                    if not chunk:
                        break
                    total_size = self._write_chunk(
                        destination_handle,
                        hasher,
                        chunk,
                        total_size,
                    )

                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        except DocumentUploadError:
            self._remove_partial_file(destination)
            raise
        except OSError as exc:
            self._remove_partial_file(destination)
            raise DocumentStorageError() from exc
        except Exception as exc:
            self._remove_partial_file(destination)
            raise DocumentStorageError() from exc

        return StoredDocument(
            storage_key=storage_key,
            original_filename=safe_filename,
            content_type=content_type,
            file_size_bytes=total_size,
            sha256_checksum=hasher.hexdigest(),
        )

    def path_for(self, storage_key: str) -> Path:
        if not isinstance(storage_key, str):
            raise InvalidStorageKeyError("Storage key must be a string")

        key = storage_key.strip()
        if (
            not key
            or key in {".", ".."}
            or "\x00" in key
            or "/" in key
            or "\\" in key
        ):
            raise InvalidStorageKeyError("Invalid storage key")

        candidate = (self.root_dir / key).resolve()
        if candidate.parent != self.root_dir:
            raise InvalidStorageKeyError("Storage key escapes the storage root")
        return candidate

    def delete(self, storage_key: str) -> None:
        destination = self.path_for(storage_key)
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            raise DocumentStorageError() from exc

    def _write_chunk(
        self,
        destination_handle: BinaryIO,
        hasher: Any,
        chunk: bytes,
        current_size: int,
    ) -> int:
        new_size = current_size + len(chunk)
        if new_size > self.max_upload_size_bytes:
            raise DocumentTooLargeError()

        hasher.update(chunk)
        destination_handle.write(chunk)
        return new_size

    def _ensure_root(self) -> None:
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocumentStorageError() from exc

        if not self.root_dir.is_dir():
            raise DocumentStorageError()

    def _create_exclusive_destination(
        self,
    ) -> tuple[str, Path, BinaryIO]:
        for _ in range(10):
            storage_key = f"{secrets.token_hex(32)}.pdf"
            destination = self.path_for(storage_key)
            try:
                destination_handle = destination.open("xb")
            except FileExistsError:
                continue
            except OSError as exc:
                raise DocumentStorageError() from exc
            return storage_key, destination, destination_handle

        raise DocumentStorageError()

    def _remove_partial_file(self, destination: Path) -> None:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass


def get_document_storage() -> PrivateDocumentStorage:
    return PrivateDocumentStorage(
        settings.document_storage_dir,
        max_upload_size_bytes=settings.document_max_upload_size_bytes,
        allowed_content_types=settings.allowed_document_content_types,
    )
