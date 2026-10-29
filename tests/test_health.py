from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


def test_health_does_not_call_the_vendor(client, vendor):
    client.get("/health")
    assert not vendor.calls


def test_context_path_prefixes_every_route(settings, vendor):
    settings = settings.model_copy(update={"context_path": "/las-api"})
    with TestClient(create_app(settings)) as c:
        assert c.get("/las-api/health").status_code == 200
        assert c.get("/las-api/openapi.json").status_code == 200
        assert c.get("/las-api/api/v1/services").status_code != 404
        assert c.get("/health").status_code == 404
        assert c.get("/api/v1/services").status_code == 404
