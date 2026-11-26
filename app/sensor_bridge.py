"""Translate the existing sensor dashboard without inferring missing bay wiring."""
from __future__ import annotations

from datetime import datetime, timezone

from .parking_store import SPACES


def dashboard_sensor_batch(snapshot: dict, mapping: dict[str, str], now: datetime | None = None) -> dict | None:
    valid_spaces = {space for space, _ in SPACES}
    if not mapping or set(mapping) - {"a1", "m1"} or set(mapping.values()) - valid_spaces:
        raise ValueError("Map explicit a1/m1 channels to valid VIP-1/2 or NORMAL-1/2/3 IDs")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Two sensor channels cannot map to the same bay")
    source = snapshot.get("sources", {}).get("uno", {})
    if source.get("fresh") is not True or source.get("payload_valid") is not True:
        return None
    try:
        stamp = datetime.fromisoformat(source["last_received_utc"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    if not -5 <= age < 10:
        return None
    updates = []
    for channel, space_id in mapping.items():
        if channel == "m1" and source.get("m1_installed") is not True:
            continue
        occupied = source.get(f"{channel}_occupied")
        if source.get(f"{channel}_occupied_valid") is True and type(occupied) is bool:
            updates.append({"space_id": space_id, "occupied": occupied, "observed_at": stamp.astimezone(timezone.utc).isoformat()})
    return {"schema_version": 1, "source": "sensor-dashboard", "spaces": updates} if updates else None
