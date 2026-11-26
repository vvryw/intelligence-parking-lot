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
    """Small, injectable wrapper around the official Google Gen AI Python SDK."""

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
        # Earlier project revisions documented 30 seconds, which is too short
        # once the SDK's transient retry/backoff time is included.
        self.timeout_seconds = (
            90.0 if configured_timeout.strip() == "30" else max(1.0, float(configured_timeout))
        )
        self._generate = generate
        self._client: Any = None

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
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise GeminiServiceError("google-genai is not installed; install requirements.txt") from exc
        use_fallback = self.model == DEFAULT_GEMINI_MODEL
        if self._client is None:
            http_options = types.HttpOptions(timeout=round(self.timeout_seconds * 1000))
            if use_fallback:
                # Try the fallback directly instead of waiting for Google's Retry-After.
                http_options.retry_options = types.HttpRetryOptions(attempts=0)
            self._client = genai.Client(
                api_key=api_key,
                http_options=http_options,
            )
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
        if use_fallback:
            request["timeout"] = min(self.timeout_seconds, ENTRANCE_ATTEMPT_TIMEOUT_SECONDS)

        model = self.model
        try:
            try:
                payload = self._request_json(model, request)
            except Exception as exc:
                status = getattr(exc, "status_code", getattr(exc, "code", None))
                timed_out = isinstance(exc, httpx.TimeoutException) or isinstance(
                    exc.__cause__, httpx.TimeoutException
                )
                temporary_error = timed_out or status in (
                    408, 429, 500, 502, 503, 504,
                )
                if not use_fallback or not temporary_error:
                    raise
                model = FALLBACK_GEMINI_MODEL
                payload = self._request_json(model, request)
        except GeminiServiceError:
            raise
        except Exception as exc:
            raise GeminiServiceError(f"Gemini request failed: {exc}") from exc
        return payload, model

    def _request_json(self, model: str, request: dict[str, Any]) -> dict[str, Any]:
        thinking_level = THINKING_LEVELS.get(model)
        response = self._client.interactions.create(
            model=model,
            **({"generation_config": {"thinking_level": thinking_level}} if thinking_level else {}),
            **request,
        )
        if not response.output_text:
            raise GeminiServiceError("Gemini returned an empty response")
        payload = json.loads(response.output_text)
        if not isinstance(payload, dict):
            raise GeminiServiceError("Gemini returned a non-object JSON response")
        return payload
