from __future__ import annotations

import json
import os
from base64 import b64encode
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from dotenv import load_dotenv

# Loads only this project's .env and never overwrites a key already supplied by
# the process environment (for example, CI or a PowerShell $env: assignment).
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


class GeminiServiceError(RuntimeError):
    pass


class GeminiJSONService:
    """Small, injectable wrapper around the official Google Gen AI Python SDK."""

    def __init__(
        self,
        model: str | None = None,
        generate: Callable[[np.ndarray, str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        configured_model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        # Gemini retired this legacy model for new users. Preserve configurability
        # while allowing existing .env files created by earlier project versions.
        self.model = (
            "gemini-3.8-flash"
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
        if self._generate is not None:
            return self._generate(image, prompt, response_schema)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise GeminiServiceError("GEMINI_API_KEY is not configured")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise GeminiServiceError("google-genai is not installed; install requirements.txt") from exc
        if self._client is None:
            # SDK 2.x expects HttpOptions.timeout in milliseconds. The SDK's own
            # transient-error retry/backoff stays enabled inside this deadline.
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=round(self.timeout_seconds * 1000)),
            )
        encoded, buffer = cv2.imencode(".jpg", image)
        if not encoded:
            raise GeminiServiceError("Could not encode image for Gemini")
        try:
            response = self._client.interactions.create(
                model=self.model,
                input=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "data": b64encode(buffer.tobytes()).decode("ascii"),
                        "mime_type": "image/jpeg",
                    },
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": response_schema,
                },
            )
            if not response.output_text:
                raise GeminiServiceError("Gemini returned an empty response")
            payload = json.loads(response.output_text)
        except GeminiServiceError:
            raise
        except Exception as exc:
            raise GeminiServiceError(f"Gemini request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise GeminiServiceError("Gemini returned a non-object JSON response")
        return payload
