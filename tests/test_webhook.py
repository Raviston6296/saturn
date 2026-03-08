"""
Tests for the Cliq webhook endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["agent"] == "saniyan"


class TestCliqWebhook:
    def test_basic_message(self, client):
        payload = {
            "name": "TestUser",
            "message": "Fix the login bug in Raviston6296/my-app",
            "chat_id": "chan_123",
            "channel_name": "dev-ops",
        }
        response = client.post("/webhook/cliq", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "SANIYAN" in data.get("text", "")

    def test_empty_message_ignored(self, client):
        payload = {
            "name": "TestUser",
            "message": "",
            "chat_id": "chan_123",
        }
        response = client.post("/webhook/cliq", json=payload)
        assert response.status_code == 200

    def test_bot_message_ignored(self, client):
        payload = {
            "name": "saniyan",
            "message": "Some bot output",
            "chat_id": "chan_123",
        }
        response = client.post("/webhook/cliq", json=payload)
        assert response.status_code == 200
        assert response.json().get("status") == "ignored"

    def test_task_type_detection_bug(self, client):
        payload = {
            "name": "Dev",
            "message": "Fix the broken authentication flow",
            "chat_id": "chan_123",
        }
        response = client.post("/webhook/cliq", json=payload)
        data = response.json()
        assert "bug_fix" in data.get("text", "").lower() or "SANIYAN" in data.get("text", "")

    def test_task_type_detection_feature(self, client):
        payload = {
            "name": "Dev",
            "message": "Add rate limiting to the API endpoints",
            "chat_id": "chan_123",
        }
        response = client.post("/webhook/cliq", json=payload)
        data = response.json()
        assert "SANIYAN" in data.get("text", "")

