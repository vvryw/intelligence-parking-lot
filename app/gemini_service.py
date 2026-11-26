from __future__ import annotations

import json
import os
from base64 import b64encode
from pathlib import Path
from typing import Any, Callable

import cv2
import httpx
import numpy as np
from dotenv import load_dotenv

# Loads only this project's .env and never overwrites a key already supplied by
# the process environment (for example, CI or a PowerShell $env: assignment).
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"
FALLBACK_GEMINI_MODEL = "gemini-3.1-flash-lite"
ENTRANCE_ATTEMPT_TIMEOUT_SECONDS = 8.0
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
THINKING_LEVELS = {
    "gemini-4-hydrogen": "low",
    "gemini-3.8-flash": "low",
    "gemini-3.7-flash": "low",
    "gemini-3.6-flash": "minimal",
    "gemini-3.5-flash": "minimal",
    "gemini-3.5-flash-lite": "minimal",
    "gemini-3.1-flash-lite": "minimal",
}


class GeminiServiceError(RuntimeError):
    pass


class GeminiJSONService:
    """Send image and structured JSON requests to the Gemini Interactions API."""

    def __init__(
        self,
        model: str | None = None,
        generate: Callable[[np.ndarray, str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        configured_model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        # Gemini retired this legacy model for new users. Preserve configurability
        # while allowing existing .env files created by earlier project versions.
        self.model = (
            DEFAULT_GEMINI_MODEL
            if configured_model.removeprefix("models/") == "gemini-2.5-flash"
            else configured_model.removeprefix("models/")
        )
        configured_timeout = os.getenv("GEMINI_TIMEOUT_SECONDS", "90")
        # Preserve the prior timeout for environments using the old 30-second default.
        self.timeout_seconds = (
            90.0 if configured_timeout.strip() == "30" else max(1.0, float(configured_timeout))
        )
        self._generate = generate
        self._client = httpx.Client()

    def analyze(
        self, image: np.ndarray, prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        payload, _ = self.analyze_with_model(image, prompt, response_schema)
        return payload

    def analyze_with_model(
        self, image: np.ndarray, prompt: str, response_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        if self._generate is not None:
            return self._generate(image, prompt, response_schema), self.model
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise GeminiServiceError("GEMINI_API_KEY is not configured")
        use_fallback = self.model == DEFAULT_GEMINI_MODEL
        encoded, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not encoded:
            raise GeminiServiceError("Could not encode image for Gemini")
        request = {
            "store": False,
            "input": [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "data": b64encode(buffer.tobytes()).decode("ascii"),
                    "mime_type": "image/jpeg",
                },
            ],
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_schema,
            },
        }
        timeout = (
            min(self.timeout_seconds, ENTRANCE_ATTEMPT_TIMEOUT_SECONDS)
            if use_fallback else self.timeout_seconds
        )

        model = self.model
        try:
            try:
                payload = self._request_json(model, request, api_key, timeout)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                temporary_error = status is None or status in (
                    408, 429, 500, 502, 503, 504,
                )
                if not use_fallback or not temporary_error:
                    raise
                model = FALLBACK_GEMINI_MODEL
                payload = self._request_json(model, request, api_key, timeout)
        except GeminiServiceError:
            raise
        except Exception as exc:
            raise GeminiServiceError(f"Gemini request failed: {exc}") from exc
        return payload, model

    def _request_json(
        self, model: str, request: dict[str, Any], api_key: str, timeout: float
    ) -> dict[str, Any]:
        thinking_level = THINKING_LEVELS.get(model)
        body = {"model": model, **request}
        if thinking_level:
            body["generation_config"] = {"thinking_level": thinking_level}
        # A direct request lets the gate try the fallback without SDK backoff.
        response = self._client.post(
            GEMINI_INTERACTIONS_URL,
            headers={"x-goog-api-key": api_key},
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        text = "".join(
            part["text"]
            for step in response.json().get("steps", [])
            if step.get("type") == "model_output"
            for part in step.get("content", [])
            if part.get("type") == "text"
        )
        if not text:
            raise GeminiServiceError("Gemini returned an empty response")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise GeminiServiceError("Gemini returned a non-object JSON response")
        return payload
