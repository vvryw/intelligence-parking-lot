from concurrent.futures import ThreadPoolExecutor

import pytest

from app.parking_store import ParkingStore


@pytest.fixture
def store(tmp_path):
    return ParkingStore(tmp_path / "parking.sqlite3", tmp_path / "photos")


def observe(store, plate, vehicle="car", camera="entrance", eligible=True):
    return store.save_observation(camera, plate, b"test-photo", b"test-crop", {
        "license_plate": plate, "vehicle_type": vehicle, "eligible": eligible,
    })


def commit(store, plate, vehicle="car", camera="entrance", eligible=True):
    obs = observe(store, plate, vehicle, camera, eligible)
    return store.commit(obs["id"], obs["id"])


def test_capacity_is_persistent_and_spaces_do_not_cross_types(store):
    assert [commit(store, f"NORMAL{i}")["space_id"] for i in range(3)] == ["NORMAL-1", "NORMAL-2", "NORMAL-3"]
    denied = commit(store, "NORMAL4")
    assert not denied["approved"] and denied["reason"] == "normal_full"
    assert denied["gate"]["action"] == "keep_closed"
    assert store.status()["capacity"]["vip"]["available"] == 2
    assert commit(store, "SPORT1", "sportcar")["space_id"] == "VIP-1"
    assert commit(store, "SUPER1", "supercar")["space_id"] == "VIP-2"
    assert commit(store, "NORMAL0", camera="exit")["approved"]
    assert not commit(store, "SUPER2", "supercar")["approved"]
    restored = ParkingStore(store.database, store.storage)
    assert restored.status()["capacity"]["normal"]["available"] == 1
    assert restored.status()["capacity"]["vip"]["available"] == 0


def test_retries_duplicate_plates_and_exits_are_idempotent(store):
    obs = observe(store, "AB385")
    first = store.commit(obs["id"], "stable-request")
    replay = store.commit(obs["id"], "another-request")
    assert first["id"] == replay["id"]
    assert replay["replayed"] and not replay["gate"]["command_created"]
    assert commit(store, "AB385")["reason"] == "already_inside"
    assert commit(store, "UNKNOWN", camera="exit")["reason"] == "no_active_session"
    assert store.status()["capacity"]["normal"]["available"] == 2
    assert commit(store, "AB385", camera="exit")["approved"]
    assert commit(store, "AB385", camera="exit")["reason"] == "no_active_session"
    assert store.status()["capacity"]["normal"]["available"] == 3
    assert commit(store, "AB385")["approved"]  # A new visit after exit.
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM gate_commands").fetchone()[0] == 3


def test_uncertain_unsupported_and_reused_request_cannot_open_gate(store):
    assert not commit(store, "LOWCONF", eligible=False)["approved"]
    assert not commit(store, "UNKNOWNCLASS", vehicle="unknown")["approved"]
    one, two = observe(store, "ONE"), observe(store, "TWO")
    store.commit(one["id"], "same-request")
    with pytest.raises(ValueError):
        store.commit(two["id"], "same-request")
    assert store.status()["capacity"]["normal"]["occupied"] == 1


def test_concurrent_entries_cannot_overbook(store):
    observations = [observe(store, f"CAR{i}") for i in range(10)] + [observe(store, f"VIP{i}", "supercar") for i in range(6)]
    with ThreadPoolExecutor(max_workers=8) as workers:
        decisions = list(workers.map(lambda obs: store.commit(obs["id"], obs["id"]), observations))
    assert sum(d["approved"] for d in decisions) == 5
    assert len({d["space_id"] for d in decisions if d["approved"]}) == 5
    assert all(d["space_id"].startswith("NORMAL") for d in decisions if d["approved"] and d["vehicle_type"] == "car")


def test_photo_roundtrip_and_audit_history(store):
    obs = observe(store, "กข1234")
    assert store.media_path(obs["photo_id"]).read_bytes() == b"test-photo"
    assert store.media_path(obs["crop_id"]).read_bytes() == b"test-crop"
    store.commit(obs["id"], obs["id"])
    commit(store, "กข1234", camera="exit")
    history = store.history()
    assert history["sessions"][0]["status"] == "left"
    assert history["sessions"][0]["entrance_photo_url"]
    assert history["sessions"][0]["exit_photo_url"]
    assert len(history["events"]) == 2
