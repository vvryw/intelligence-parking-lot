"""Optional authenticated bridge: sensor dashboard -> parking occupancy API."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from dotenv import dotenv_values, load_dotenv

from app.sensor_bridge import dashboard_sensor_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True, type=Path, help="JSON mapping of physically verified a1/m1 channels to bay IDs")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:5000")
    parser.add_argument("--parking-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dashboard-env", type=Path, help="Sensor project's private .env containing dashboard credentials")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.getenv("SENSOR_INTEGRATION_TOKEN")
    if not token:
        parser.error("Configure SENSOR_INTEGRATION_TOKEN in the private .env")
    credentials = dotenv_values(args.dashboard_env) if args.dashboard_env else os.environ
    user = credentials.get("DASHBOARD_USERNAME") or credentials.get("GRAFANA_USERNAME")
    password = credentials.get("DASHBOARD_PASSWORD") or credentials.get("GRAFANA_PASSWORD")
    if not user or not password:
        parser.error("Provide --dashboard-env or export the existing dashboard Basic Auth credentials")
    mapping = json.loads(args.mapping.read_text())
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        while True:
            try:
                source = client.get(args.dashboard_url.rstrip("/") + "/api/latest", auth=(user, password))
                source.raise_for_status()
                batch = dashboard_sensor_batch(source.json(), mapping)
                if batch:
                    response = client.post(args.parking_url.rstrip("/") + "/parking/sensors", json=batch,
                                           headers={"X-Integration-Key": token})
                    response.raise_for_status()
                    print("Forwarded", len(batch["spaces"]), "fresh bay observations", flush=True)
                else:
                    print("No fresh installed sensor channels; no occupancy fabricated", flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                print("Sensor bridge unavailable:", type(exc).__name__, flush=True)
                if args.once:
                    raise SystemExit(1)
            if args.once:
                break
            sleep(2)


if __name__ == "__main__":
    main()
