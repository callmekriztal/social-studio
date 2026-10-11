from fastapi.testclient import TestClient


def test_health_check_root_endpoint(client: TestClient):
    """Verify that GET /health returns 200 and healthy database status."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "healthy"
    assert "environment" in data
    assert "timestamp" in data


def test_health_check_v1_prefixed_endpoint(client: TestClient):
    """Verify that GET /api/v1/health is also reachable and returns 200."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "healthy"


def test_root_redirects_to_docs(client: TestClient):
    """Verify that root / redirects to OpenAPI docs."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"
