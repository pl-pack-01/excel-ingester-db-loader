"""Tests for Flask app."""

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Excel Data Ingestor" in resp.data


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
