from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Therapist Credential Verification API"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://postgres@localhost:5432/therapist_credentials"
    )
    document_storage_dir: Path = (
        PROJECT_ROOT / "storage" / "private_documents"
    )
    document_max_upload_size_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
    )
    document_allowed_content_types: str = "application/pdf"
    extracted_text_storage_dir: Path = (
        PROJECT_ROOT / "storage" / "private_extracted_text"
    )
    pdf_extraction_max_pages: int = Field(default=30, gt=0)
    pdf_extraction_max_text_bytes: int = Field(
        default=2 * 1024 * 1024,
        gt=0,
    )
    pdf_extraction_render_dpi: int = Field(default=200, ge=72, le=300)
    pdf_extraction_chunk_size_chars: int = Field(default=2000, ge=200)
    pdf_extraction_chunk_overlap_chars: int = Field(default=200, ge=0)
    pdf_extraction_ocr_min_chars: int = Field(default=50, ge=0)
    pdf_extraction_ocr_page_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
    )
    pdf_extraction_ocr_total_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
    )
    tesseract_cmd: str | None = None
    tesseract_data_dir: Path = PROJECT_ROOT / "storage" / "tessdata"
    tesseract_language: str = "eng"

    @property
    def allowed_document_content_types(self) -> tuple[str, ...]:
        content_types = {
            content_type.strip().lower()
            for content_type in self.document_allowed_content_types.split(",")
            if content_type.strip()
        }
        return tuple(sorted(content_types))


settings = Settings()


def get_settings() -> Settings:
    return settings
