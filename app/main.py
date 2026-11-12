from fastapi import FastAPI

from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.info import API_VERSION, PROJECT_DESCRIPTION
from app.api.routes.info import router as info_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    description=PROJECT_DESCRIPTION,
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": f"Welcome to the {settings.app_name}",
        "status": "running",
    }


app.include_router(health_router)
app.include_router(info_router, prefix=settings.api_v1_prefix)
app.include_router(documents_router, prefix=settings.api_v1_prefix)
