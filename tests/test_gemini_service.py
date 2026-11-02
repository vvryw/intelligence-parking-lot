from types import SimpleNamespace

import numpy as np

from app.gemini_service import GeminiJSONService


class FakeInteractions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text='{"vehicle_type":"car"}')


def test_service_uses_interactions_api_and_structured_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    interactions = FakeInteractions()
    service = GeminiJSONService()
    service._client = SimpleNamespace(interactions=interactions)

    result = service.analyze(
        np.zeros((10, 10, 3), dtype=np.uint8),
        "classify the vehicle",
        {"type": "object"},
    )

    assert result == {"vehicle_type": "car"}
    assert service.model == "gemini-3.8-flash"
    assert service.timeout_seconds == 90.0
    assert interactions.kwargs["model"] == "gemini-3.8-flash"
    assert interactions.kwargs["input"][0]["type"] == "text"
    assert interactions.kwargs["input"][1]["type"] == "image"
    assert interactions.kwargs["response_format"]["mime_type"] == "application/json"
