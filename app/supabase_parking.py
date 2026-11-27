from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

import httpx

from .parking_store import ParkingStore, ROOT


class SupabaseParkingStore:
    """Supabase is authoritative; gate decisions fail closed on cloud errors."""

    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "intelligence-parking-lot")
        if not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co", self.url) or not key:
            raise ValueError("Configure SUPABASE_URL and the server-only SUPABASE_SECRET_KEY")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", self.bucket):
            raise ValueError("Invalid Supabase bucket name")
        self.require_sensors = os.getenv("PARKING_REQUIRE_FRESH_SENSORS", "false").lower() == "true"
        self.cache = ROOT / "data/storage/cloud-cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        headers = {"apikey": key}
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        self.client = httpx.Client(base_url=self.url, headers=headers, timeout=httpx.Timeout(20, connect=5), follow_redirects=False)

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise OSError("Supabase connection unavailable") from exc
        if not response.is_success:
            try:
                message = response.json().get("message", "")
            except (ValueError, AttributeError):
                message = ""
            if message == "Observation not found":
                raise KeyError(message)
            if message.startswith("Request ID was already used"):
                raise ValueError(message)
            raise OSError(f"Supabase request failed (HTTP {response.status_code})")
        return response

    def _rpc(self, name: str, params: dict):
        return self._request("POST", f"/rest/v1/rpc/{name}", json=params).json()

    def save_observation(self, camera: str, fingerprint: str, photo: bytes, crop: bytes | None, recognition: dict) -> dict:
        observation_id = str(uuid4())
        media = []
        for kind, data in (("photo", photo), ("crop", crop)):
            if data is None:
                continue
            media_id = str(uuid4())
            object_path = f"observations/{observation_id}/{kind}.jpg"
            self._request("POST", f"/storage/v1/object/{self.bucket}/{object_path}", content=data,
                          headers={"Content-Type": "image/jpeg", "x-upsert": "false"})
            media.append({"id": media_id, "kind": kind, "object_path": object_path, "bytes": len(data)})
        # A timeout may mean this RPC committed but its response was lost. Never
        # delete uploaded images on an ambiguous error: the database may reference them.
        return self._rpc("parking_create_observation", {
            "p_id": observation_id, "p_camera": camera, "p_fingerprint": fingerprint,
            "p_recognition": recognition, "p_media": media,
        })

    def observation(self, observation_id: str) -> dict:
        result = self._rpc("parking_observation", {"p_id": observation_id})
        if not result:
            raise KeyError("Observation not found")
        return result

    def commit(self, observation_id: str, request_id: str) -> dict:
        return self._rpc("parking_commit_event", {
            "p_observation_id": observation_id, "p_request_id": request_id,
            "p_require_sensors": self.require_sensors,
        })

    def status(self) -> dict:
        result = self._rpc("parking_current_state", {"p_require_sensors": self.require_sensors})
        result["cloud_project"] = self.url.split("//", 1)[1].split(".", 1)[0]
        return result

    def history(self, limit: int = 30) -> dict:
        return self._rpc("parking_history", {"p_limit": limit})

    def record_sensors(self, updates: list[dict], source: str) -> dict:
        return self._rpc("parking_record_sensors", {"p_updates": updates, "p_source": source})

    def media_path(self, media_id: str) -> Path:
        rows = self._request("GET", "/rest/v1/parking_media", params={"id": f"eq.{media_id}", "select": "object_path"}).json()
        if not rows:
            raise KeyError("Photo not found")
        path = self.cache / f"{media_id}.jpg"
        if not path.is_file():
            response = self._request("GET", f"/storage/v1/object/authenticated/{self.bucket}/{rows[0]['object_path']}")
            temporary = self.cache / f"{uuid4()}.tmp"
            try:
                temporary.write_bytes(response.content)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return path


def create_parking_store():
    backend = os.getenv("PARKING_BACKEND", "local")
    if os.getenv("PARKING_PASSAGE_MODE", "false").lower() == "true":
        database = os.getenv("PARKING_DATABASE_PATH")
        photos = os.getenv("PARKING_STORAGE_PATH")
        if backend != "local" or not database or not photos or not os.getenv("PARKING_PASSAGE_KEY"):
            raise ValueError("Passage mode requires local storage, explicit test paths, and PARKING_PASSAGE_KEY")
        from .passage_store import PassageParkingStore

        return PassageParkingStore(database, photos)
    if backend == "supabase":
        return SupabaseParkingStore()
    if backend == "local":
        return ParkingStore()
    raise ValueError("PARKING_BACKEND must be local or supabase")
