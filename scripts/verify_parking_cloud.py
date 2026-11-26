"""Exercise the simulator with isolated temporary fixtures in an EMPTY cloud lot."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from dotenv import load_dotenv

from app.parking_recognition import jpeg_bytes
from app.supabase_parking import SupabaseParkingStore


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    store = SupabaseParkingStore()
    before = store.status()
    if before["gate_mode"] != "simulation" or any(before["capacity"][kind]["available"] != total for kind, total in (("vip", 2), ("normal", 3))):
        raise RuntimeError("Verification requires an empty lot and the gate simulator; existing visits are preserved")
    run = uuid4().hex[:10].upper()
    observations = []
    image = np.full((48, 160, 3), 230, np.uint8)
    cv2.putText(image, "TEST ONLY", (4, 30), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 1)
    photo = jpeg_bytes(image)
    checks = []

    def make(plate, vehicle="car", camera="entrance", eligible=True):
        obs = store.save_observation(camera, run, photo, None, {
            "license_plate": plate, "vehicle_type": vehicle, "eligible": eligible, "verification_run": run,
        })
        observations.append(obs)
        return obs

    def admit(obs):
        return store.commit(obs["id"], f"verify-{run}-{obs['id']}")

    def clean():
        if not observations:
            return
        ids = [obs["id"] for obs in observations]
        select = "in.(" + ",".join(ids) + ")"
        event_rows = store._request("GET", "/rest/v1/parking_events", params={"observation_id": select, "select": "id"}).json()
        if event_rows:
            event_ids = "in.(" + ",".join(row["id"] for row in event_rows) + ")"
            store._request("DELETE", "/rest/v1/parking_gate_commands", params={"event_id": event_ids})
        store._request("DELETE", "/rest/v1/parking_events", params={"observation_id": select})
        store._request("DELETE", "/rest/v1/parking_sessions", params={"entrance_observation_id": select})
        store._request("DELETE", "/rest/v1/parking_observations", params={"id": select})
        media = [obs["photo_id"] for obs in observations]
        store._request("DELETE", "/rest/v1/parking_media", params={"id": "in.(" + ",".join(media) + ")"})
        store._request("DELETE", f"/storage/v1/object/{store.bucket}", json={"prefixes": [f"observations/{value}/photo.jpg" for value in ids]})
        for value in media:
            (store.cache / f"{value}.jpg").unlink(missing_ok=True)

    try:
        normal = [make(f"TEST{run}N{i}") for i in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            decisions = list(pool.map(admit, normal))
        assert sum(item["approved"] for item in decisions) == 3
        assert all(item["space_id"].startswith("NORMAL") for item in decisions if item["approved"])
        assert store.status()["capacity"]["vip"]["available"] == 2
        checks.append("Concurrent normal arrivals: exactly 3 admitted; no use of free VIP spaces")
        vip = [make(f"TEST{run}V{i}", "sportcar" if i == 0 else "supercar") for i in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            vip_decisions = list(pool.map(admit, vip))
        assert sum(item["approved"] for item in vip_decisions) == 2
        assert all(item["space_id"].startswith("VIP") for item in vip_decisions if item["approved"])
        checks.append("Sports/supercars: exactly 2 VIP admissions; next VIP denied")
        entry = next(item for item in decisions if item["approved"])
        replay = store.commit(entry["observation_id"], "retry-" + run)
        assert replay["replayed"] and not replay["gate"]["command_created"]
        exit_obs = make(entry["plate"], camera="exit")
        assert admit(exit_obs)["approved"]
        assert store.status()["capacity"]["normal"]["available"] == 1
        assert not admit(make(f"TEST{run}EXTRA", "supercar"))["approved"]
        checks.append("Matched exit released normal bay; VIP cannot take that free normal bay")
        assert admit(make(f"TEST{run}UNKNOWN", camera="exit"))["reason"] == "no_active_session"
        assert not admit(make(f"TEST{run}REVIEW", eligible=False))["approved"]
        assert store.commit(exit_obs["id"], "exit-retry-" + run)["replayed"]
        checks.append("Unknown exits, uncertain recognition and duplicate retries cannot create extra opens")
        downloaded = store.media_path(normal[0]["photo_id"])
        assert cv2.imread(str(downloaded)).shape == image.shape
        checks.append("Private Supabase photo upload and authenticated download decoded successfully")
        for check in checks:
            print("PASS:", check, flush=True)
    finally:
        clean()
    after = store.status()
    assert after["capacity"] == before["capacity"]
    assert after["archived_observations"] == before["archived_observations"]
    report = {"checks": checks, "fixtures_removed": True, "capacity": after["capacity"]}
    Path("reports/parking_cloud_verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: only this run's temporary records/photos removed; original lot restored", flush=True)


if __name__ == "__main__":
    main()
