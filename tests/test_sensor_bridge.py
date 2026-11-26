from datetime import datetime, timedelta, timezone

import pytest

from app.sensor_bridge import dashboard_sensor_batch


def snapshot():
    return {"sources": {"uno": {"fresh": True, "payload_valid": True,
            "last_received_utc": "2026-11-27T00:00:00+00:00", "a1_occupied_valid": True,
            "a1_occupied": False, "m1_installed": False, "m1_occupied_valid": False}}}


def test_only_explicitly_mapped_fresh_installed_channels_are_forwarded():
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    result = dashboard_sensor_batch(snapshot(), {"a1": "NORMAL-1", "m1": "VIP-1"}, now)
    assert result["spaces"] == [{"space_id": "NORMAL-1", "occupied": False, "observed_at": now.isoformat()}]
    assert dashboard_sensor_batch(snapshot(), {"a1": "NORMAL-1"}, now + timedelta(seconds=11)) is None


def test_unknown_invalid_and_ambiguous_channels_do_not_become_free_spaces():
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    data = snapshot(); data["sources"]["uno"]["payload_valid"] = False
    assert dashboard_sensor_batch(data, {"a1": "NORMAL-1"}, now) is None
    with pytest.raises(ValueError):
        dashboard_sensor_batch(snapshot(), {"a1": "NORMAL-1", "m1": "NORMAL-1"}, now)
    with pytest.raises(ValueError):
        dashboard_sensor_batch(snapshot(), {"unknown": "VIP-1"}, now)
