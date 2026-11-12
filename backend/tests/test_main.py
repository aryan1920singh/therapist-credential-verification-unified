from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_root_returns_welcome_response() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": f"Welcome to the {settings.app_name}",
        "status": "running",
    }


def test_health_returns_healthy_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["application"] == settings.app_name
    assert body["environment"] == settings.app_env


def test_info_returns_application_information() -> None:
    response = client.get("/api/v1/info")

    assert response.status_code == 200
    assert response.json() == {
        "application": settings.app_name,
        "environment": settings.app_env,
        "version": "1.0.0",
        "description": (
            "Backend API for the Therapist Credential Verification System."
        ),
    }


def test_docs_are_available() -> None:
    response = client.get("/docs")

    assert response.status_code == 200


def test_unknown_route_returns_not_found() -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
