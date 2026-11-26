import json

import httpx
import numpy as np
import pytest

from app.gemini_service import GeminiJSONService


def model_response(vehicle_type):
    return httpx.Response(200, json={"steps": [
        {"type": "thought", "content": [{"type": "text", "text": "ignored"}]},
        {"type": "model_output", "content": [
            {"type": "text", "text": json.dumps({"vehicle_type": vehicle_type})},
        ]},
    ]})


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    return GeminiJSONService()


def test_service_uses_interactions_api_and_structured_json(service):
    requests = []

    def respond(request):
        requests.append(request)
        return model_response("car")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service._client = client
        result = service.analyze(
            np.zeros((10, 10, 3), dtype=np.uint8),
            "classify the vehicle",
            {"type": "object"},
        )

    assert result == {"vehicle_type": "car"}
    assert service.model == "gemini-4-hydrogen"
    assert service.timeout_seconds == 90.0
    request, = requests
    assert request.url.path == "/v1beta/interactions"
    assert request.headers["x-goog-api-key"] == "test-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-4-hydrogen"
    assert body["generation_config"] == {"thinking_level": "low"}
    assert body["store"] is False
    assert body["input"][0]["type"] == "text"
    assert body["input"][1]["type"] == "image"
    assert body["response_format"]["mime_type"] == "application/json"


@pytest.mark.parametrize("failure", ["overload", "timeout"])
def test_default_model_falls_back_once_on_overload_or_timeout(service, failure):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            if failure == "overload":
                return httpx.Response(503, headers={"Retry-After": "30"})
            raise httpx.ReadTimeout("timed out", request=request)
        return model_response("sportcar")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service._client = client
        payload, model = service.analyze_with_model(
            np.zeros((10, 10, 3), dtype=np.uint8), "classify the vehicle", {"type": "object"}
        )

    assert payload == {"vehicle_type": "sportcar"}
    assert model == "gemini-3.1-flash-lite"
    bodies = [json.loads(request.content) for request in requests]
    assert [body["model"] for body in bodies] == ["gemini-4-hydrogen", "gemini-3.1-flash-lite"]
    assert [body["generation_config"]["thinking_level"] for body in bodies] == ["low", "minimal"]
    assert [request.extensions["timeout"]["read"] for request in requests] == [8.0, 8.0]
