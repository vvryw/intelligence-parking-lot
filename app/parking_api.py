from __future__ import annotations

import asyncio
import logging
import sqlite3
import hmac
import os
from datetime import timezone, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from .cameras import CameraRole
from .parking_recognition import ParkingRecognition
from .schemas import parse_bbox

logger = logging.getLogger(__name__)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: UUID
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)


class SensorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_id: Literal["VIP-1", "VIP-2", "NORMAL-1", "NORMAL-2", "NORMAL-3"]
    occupied: StrictBool
    observed_at: AwareDatetime


class SensorBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source: str = Field(min_length=1, max_length=64)
    spaces: list[SensorUpdate] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_spaces(self):
        if len({item.space_id for item in self.spaces}) != len(self.spaces):
            raise ValueError("Duplicate space IDs in sensor batch")
        if any((item.observed_at - datetime.now(timezone.utc)).total_seconds() > 30 for item in self.spaces):
            raise ValueError("Sensor observation timestamp is in the future")
        return self


def parking_router(service: ParkingRecognition, decode_upload) -> APIRouter:
    router = APIRouter(prefix="/parking", tags=["Parking and storage"])

    @router.get("/status")
    async def status():
        try:
            return await asyncio.to_thread(service.store.status)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Parking database unavailable; availability is unknown") from exc

    @router.get("/history")
    async def history(limit: int = Query(30, ge=1, le=100)):
        try:
            return await asyncio.to_thread(service.store.history, limit)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Parking history unavailable") from exc

    @router.post("/sensors")
    async def sensors(batch: SensorBatch, x_integration_key: str | None = Header(None)):
        token = os.getenv("SENSOR_INTEGRATION_TOKEN", "")
        if not token:
            raise HTTPException(503, "Sensor integration token is not configured")
        if not x_integration_key or not hmac.compare_digest(token, x_integration_key):
            raise HTTPException(401, "Invalid sensor integration key")
        if not hasattr(service.store, "record_sensors"):
            raise HTTPException(503, "Sensor integration requires the configured cloud backend")
        updates = [{"space_id": item.space_id, "occupied": item.occupied,
                    "observed_at": item.observed_at.astimezone(timezone.utc).isoformat()} for item in batch.spaces]
        try:
            return await asyncio.to_thread(service.store.record_sensors, updates, batch.source)
        except OSError as exc:
            raise HTTPException(503, "Could not persist sensor occupancy") from exc

    @router.get("/media/{media_id}")
    async def media(media_id: UUID):
        try:
            path = await asyncio.to_thread(service.store.media_path, str(media_id))
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "Archived photo not found") from exc
        except OSError as exc:
            raise HTTPException(503, "Photo storage is temporarily unavailable") from exc
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

    @router.post("/analyze")
    async def analyze(
        image: UploadFile = File(...), camera: CameraRole = Form("entrance"),
        plate_bbox: str | None = Form(None), previous_observation_id: UUID | None = Form(None),
    ):
        decoded = await decode_upload(image)
        try:
            return await asyncio.to_thread(
                service.analyze, decoded, camera, parse_bbox(plate_bbox),
                str(previous_observation_id) if previous_observation_id else None,
            )
        except KeyError as exc:
            raise HTTPException(404, "Previous analysis not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            logger.exception("Could not durably save parking analysis")
            raise HTTPException(503, "Database or photo storage is unavailable. Gate remains closed.") from exc

    @router.post("/commit")
    async def commit(request: CommitRequest):
        try:
            decision = await asyncio.to_thread(service.store.commit, str(request.observation_id), request.request_id)
        except KeyError as exc:
            raise HTTPException(404, "Analyze the photo before registering entry or exit") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            logger.exception("Parking transaction failed; no gate command issued")
            raise HTTPException(503, "Parking decision could not be confirmed. No new OPEN confirmed; retry the same observation.") from exc
        # A status-read failure after a committed decision must not be reported
        # as a failed admission; retries use the same observation identity.
        try:
            current = await asyncio.to_thread(service.store.status)
        except (OSError, sqlite3.Error):
            current = None
        return {"decision": decision, "parking": current}

    return router
