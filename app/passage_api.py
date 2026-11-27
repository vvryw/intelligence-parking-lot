"""Authenticated loopback HTTP API for the parking simulation worker."""

from __future__ import annotations

import hmac
import os
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9:_-]{1,96}$")]
Direction = Literal["entrance", "exit"]
passage_key = APIKeyHeader(name="X-Passage-Key", auto_error=False)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireEvent(StrictRequest):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    event_id: Identifier
    producer_id: Literal["sensor-gateway"]
    occurred_at: AwareDatetime
    arrival_id: Identifier

    @field_validator("occurred_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("Timestamp must use UTC")
        if (value - datetime.now(timezone.utc)).total_seconds() > 1:
            raise ValueError("Event timestamp is in the future")
        return value.astimezone(timezone.utc)


class Arrival(WireEvent):
    direction: Direction
    trigger: Literal["ultrasonic"]


class GateAck(WireEvent):
    ack_id: Identifier
    command_id: Identifier
    state: Literal["accepted", "rejected", "failed", "unknown"]
    reason: Annotated[str, Field(max_length=128)] | None
    status: Literal["accepted", "rejected", "failed", "unknown"] | None = None
    physical_movement: Literal[False] | None = None

    @model_validator(mode="after")
    def check_status_alias(self):
        if self.status is not None and self.status != self.state:
            raise ValueError("Acknowledgement state and status disagree")
        return self


class Feedback(WireEvent):
    direction: Direction
    command_id: Identifier | None
    state: Literal["opening", "completed", "hold", "failed"]
    passage_confirmed: StrictBool
    simulation: Literal[True]
    reason: Annotated[str, Field(max_length=128)] | None

    @field_validator("simulation", mode="before")
    @classmethod
    def require_simulation(cls, value):
        if value is not True:
            raise ValueError("Simulation feedback is required")
        return value

    @model_validator(mode="after")
    def check_feedback(self):
        if self.direction == "exit" and self.command_id is not None:
            raise ValueError("Local exit requires null command_id")
        if self.direction == "entrance" and self.state in {"opening", "completed"} and self.command_id is None:
            raise ValueError("Entrance opening and completion require the command ID")
        if self.passage_confirmed and self.state != "completed":
            raise ValueError("Only completed feedback can confirm passage")
        return self


class Preparation(StrictRequest):
    observation_id: UUID
    lcd_plate: Annotated[str, Field(pattern=r"^[ -~]{1,16}$")] | None = None
    issued_at: AwareDatetime | None = None

    @field_validator("issued_at")
    @classmethod
    def normalize_issue_time(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None


class Review(StrictRequest):
    reason: Literal["capture_or_prepare_failed", "command_ack_timeout"]


class Cancellation(StrictRequest):
    confirmed_no_passage: StrictBool


class EntranceCommand(StrictRequest):
    schema_version: Literal[1]
    event_id: Identifier
    producer_id: Literal["parking-passage-test"]
    occurred_at: AwareDatetime
    arrival_id: Identifier
    command_id: Identifier
    session_id: UUID
    decision_id: UUID
    direction: Literal["entrance"]
    action: Literal["OPEN"]
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    license_plate: str
    lcd_plate: str
    vehicle_type: Literal["car", "sportcar", "supercar"]


class ArrivalState(StrictRequest):
    arrival_id: Identifier
    direction: Direction
    decision_id: UUID
    observation_id: UUID | None
    session_id: UUID | None
    space_id: str | None
    plate: str | None
    vehicle_type: str | None
    ack_state: str | None
    state: Literal["capturing", "reserved", "matched", "denied", "review", "completed", "cancelled"]
    reason: str | None
    replayed: bool
    command: EntranceCommand | None


class FeedbackReceipt(StrictRequest):
    schema_version: Literal[1]
    event_id: Identifier
    producer_id: Literal["parking-passage-test"]
    occurred_at: AwareDatetime
    arrival_id: Identifier
    feedback_id: Identifier
    state: Literal["recorded"]


class ReceiptResponse(StrictRequest):
    receipt: FeedbackReceipt | None


def wire_dict(event: WireEvent) -> dict:
    return {**event.model_dump(mode="json"), "occurred_at": event.occurred_at.isoformat()}


def authenticate(x_passage_key: Annotated[str | None, Depends(passage_key)]):
    expected = os.environ["PARKING_PASSAGE_KEY"]
    if x_passage_key is None or not hmac.compare_digest(x_passage_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid passage key")


async def call(operation, *args):
    try:
        return await run_in_threadpool(operation, *args)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Arrival, observation, or reservation already bound"
        raise HTTPException(409, message) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "Passage ledger unavailable") from exc


def passage_router(store):
    router = APIRouter(prefix="/parking/passage", tags=["Passage simulation"],
                       dependencies=[Depends(authenticate)])

    @router.get("/health")
    async def health():
        return {"workflow": "passage", "storage": "local", "gate_mode": "simulation"}

    @router.post("/arrivals")
    async def arrival(payload: Arrival) -> ArrivalState:
        return await call(store.arrival, wire_dict(payload))

    @router.get("/arrivals/{arrival_id}")
    async def get_arrival(arrival_id: Identifier) -> ArrivalState:
        return await call(store.get_arrival, arrival_id)

    @router.post("/arrivals/{arrival_id}/prepare")
    async def prepare(arrival_id: Identifier, payload: Preparation) -> ArrivalState:
        return await call(store.prepare, arrival_id, str(payload.observation_id), payload.lcd_plate, payload.issued_at)

    @router.post("/gate/ack")
    async def gate_ack(payload: GateAck) -> ReceiptResponse:
        return await call(store.receive, "ack", wire_dict(payload))

    @router.post("/gate/feedback")
    async def gate_feedback(payload: Feedback) -> ReceiptResponse:
        return await call(store.receive, "feedback", wire_dict(payload))

    @router.post("/arrivals/{arrival_id}/review")
    async def review(arrival_id: Identifier, payload: Review) -> ArrivalState:
        return await call(store.review, arrival_id, payload.reason)

    @router.post("/arrivals/{arrival_id}/cancel")
    async def cancel(arrival_id: Identifier, payload: Cancellation) -> ArrivalState:
        if payload.confirmed_no_passage is not True:
            raise HTTPException(409, "Operator must confirm no vehicle passed")
        return await call(store.cancel, arrival_id)

    @router.post("/worker/recover")
    async def recover():
        return await call(store.recover_worker)

    return router
