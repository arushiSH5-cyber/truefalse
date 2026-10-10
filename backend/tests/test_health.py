from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.health import get_checkers
from app.main import create_app


async def _up() -> bool:
    return True


async def _down() -> bool:
    return False


@pytest.fixture
def app_factory() -> Iterator[object]:
    def make(db_ok: bool, redis_ok: bool) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_checkers] = lambda: {
            "database": _up if db_ok else _down,
            "redis": _up if redis_ok else _down,
        }
        return TestClient(app)

    yield make


def test_health_ok(app_factory) -> None:  # type: ignore[no-untyped-def]
    client = app_factory(True, True)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_trace_id_echoed(app_factory) -> None:  # type: ignore[no-untyped-def]
    client = app_factory(True, True)
    resp = client.get("/health", headers={"x-request-id": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"
    assert client.get("/health").headers["x-request-id"]


def test_ready_all_up(app_factory) -> None:  # type: ignore[no-untyped-def]
    resp = app_factory(True, True).get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"database": True, "redis": True}}


def test_ready_degraded(app_factory) -> None:  # type: ignore[no-untyped-def]
    resp = app_factory(True, False).get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"] is False
