from fastapi import APIRouter

from app.core.config import settings

API_VERSION = "1.0.0"
PROJECT_DESCRIPTION = (
    "Backend API for the Therapist Credential Verification System."
)

router = APIRouter(tags=["info"])


@router.get("/info")
def application_info() -> dict[str, str]:
    return {
        "application": settings.app_name,
        "environment": settings.app_env,
        "version": API_VERSION,
        "description": PROJECT_DESCRIPTION,
    }
