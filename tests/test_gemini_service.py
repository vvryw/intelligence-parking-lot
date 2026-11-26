from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app.gemini_service import GeminiJSONService


class FakeInteractions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text='{"vehicle_type":"car"}')


def test_service_uses_interactions_api_and_structured_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    interactions = FakeInteractions()
    service = GeminiJSONService()
    service._client = SimpleNamespace(interactions=interactions)

    result = service.analyze(
        np.zeros((10, 10, 3), dtype=np.uint8),
        "classify the vehicle",
        {"type": "object"},
    )

    assert result == {"vehicle_type": "car"}
    assert service.model == "gemini-4-hydrogen"
    assert service.timeout_seconds == 90.0
    assert interactions.kwargs["model"] == "gemini-4-hydrogen"
    assert interactions.kwargs["generation_config"] == {"thinking_level": "low"}
    assert interactions.kwargs["store"] is False
    assert interactions.kwargs["input"][0]["type"] == "text"
    assert interactions.kwargs["input"][1]["type"] == "image"
    assert interactions.kwargs["response_format"]["mime_type"] == "application/json"


class ModelOverloaded(RuntimeError):
    status_code = 503


@pytest.mark.parametrize("failure", ["overload", "timeout"])
def test_default_model_falls_back_once_on_overload_or_timeout(monkeypatch, failure):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            if failure == "overload":
                raise ModelOverloaded()
            raise RuntimeError("SDK request timed out") from httpx.ReadTimeout("timed out")
        return SimpleNamespace(output_text='{"vehicle_type":"sportcar"}')

    service = GeminiJSONService()
    service._client = SimpleNamespace(interactions=SimpleNamespace(create=create))
    payload, model = service.analyze_with_model(
        np.zeros((10, 10, 3), dtype=np.uint8), "classify the vehicle", {"type": "object"}
    )

    assert payload == {"vehicle_type": "sportcar"}
    assert model == "gemini-3.1-flash-lite"
    assert [request["model"] for request in requests] == ["gemini-4-hydrogen", "gemini-3.1-flash-lite"]
    assert [request["generation_config"]["thinking_level"] for request in requests] == ["low", "minimal"]
    assert [request["timeout"] for request in requests] == [8.0, 8.0]
