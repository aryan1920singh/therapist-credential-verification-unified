from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from app.core.config import settings

EXTRACTED_TEXT_SUFFIX = ".json"


class ExtractedTextStorageError(Exception):
    status_code = 500
    detail = "The extracted text could not be stored."


class InvalidExtractedTextKeyError(ValueError):
    pass


class PrivateExtractedTextStorage:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()

    def save_json(self, payload: dict[str, Any]) -> str:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ExtractedTextStorageError() from exc

        self._ensure_root()
        storage_key, destination, destination_handle = (
            self._create_exclusive_destination()
        )

        try:
            with destination_handle:
                destination_handle.write(encoded)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        except OSError as exc:
            self._remove_partial_file(destination)
            raise ExtractedTextStorageError() from exc
        except Exception as exc:
            self._remove_partial_file(destination)
            raise ExtractedTextStorageError() from exc

        return storage_key

    def path_for(self, storage_key: str) -> Path:
        if not isinstance(storage_key, str):
            raise InvalidExtractedTextKeyError(
                "Extracted text key must be a string"
            )

        key = storage_key.strip()
        if (
            not key
            or key in {".", ".."}
            or "\x00" in key
            or "/" in key
            or "\\" in key
            or not key.endswith(EXTRACTED_TEXT_SUFFIX)
        ):
            raise InvalidExtractedTextKeyError("Invalid extracted text key")

        candidate = (self.root_dir / key).resolve()
        if candidate.parent != self.root_dir:
            raise InvalidExtractedTextKeyError(
                "Extracted text key escapes the storage root"
            )
        return candidate

    def delete(self, storage_key: str) -> None:
        destination = self.path_for(storage_key)
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            raise ExtractedTextStorageError() from exc

    def _ensure_root(self) -> None:
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExtractedTextStorageError() from exc

        if not self.root_dir.is_dir():
            raise ExtractedTextStorageError()

    def _create_exclusive_destination(
        self,
    ) -> tuple[str, Path, Any]:
        for _ in range(10):
            storage_key = (
                f"{secrets.token_hex(32)}{EXTRACTED_TEXT_SUFFIX}"
            )
            destination = self.path_for(storage_key)
            try:
                destination_handle = destination.open("xb")
            except FileExistsError:
                continue
            except OSError as exc:
                raise ExtractedTextStorageError() from exc
            return storage_key, destination, destination_handle

        raise ExtractedTextStorageError()

    def _remove_partial_file(self, destination: Path) -> None:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass


def get_extracted_text_storage() -> PrivateExtractedTextStorage:
    return PrivateExtractedTextStorage(
        settings.extracted_text_storage_dir,
    )
