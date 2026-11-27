"""Wire the same-laptop parking API to the sensor team's isolated MQTT v1 topics."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic
from uuid import uuid4

import httpx
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "parking/test/v1"
ARRIVAL = f"{PREFIX}/arrival"
COMMAND = f"{PREFIX}/gate/command"
ACK = f"{PREFIX}/gate/ack"
FEEDBACK = f"{PREFIX}/gate/feedback"
FEEDBACK_ACK = f"{FEEDBACK}/ack"
API_PREFIX = "/parking/passage"
logger = logging.getLogger(__name__)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def decode(payload):
    if len(payload) > 2048:
        raise ValueError("MQTT payload too large")
    def reject_nonfinite(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    data = json.loads(payload, object_pairs_hook=unique_keys, parse_constant=reject_nonfinite)
    if not isinstance(data, dict):
        raise ValueError("MQTT payload must be an object")
    return data


class PassageWorker:
    def __init__(self, http, publish, clock, lcd_map):
        self.http = http
        self.publish = publish
        self.clock = clock
        self.lcd_map = lcd_map
        self.pending = {}

    def post(self, path, payload=None):
        response = self.http.post(API_PREFIX + path, json=payload)
        response.raise_for_status()
        return response.json()

    def preflight(self):
        status = self.http.get("/parking/status")
        status.raise_for_status()
        data = status.json()
        if any(data.get(key) != value for key, value in
               (("storage", "local"), ("gate_mode", "simulation"), ("workflow", "passage"))):
            raise RuntimeError("Worker requires the isolated passage-mode API")
        self.http.get(API_PREFIX + "/health").raise_for_status()
        spec = self.http.get("/openapi.json")
        spec.raise_for_status()
        paths = spec.json()["paths"]
        if not all(API_PREFIX + path in paths for path in
                   ("/arrivals", "/arrivals/{arrival_id}/prepare", "/gate/feedback", "/gate/ack")):
            raise RuntimeError("Passage endpoints missing")

    def arrival(self, payload):
        recorded = self.post("/arrivals", payload)
        if recorded["replayed"]:
            return
        aid, role = recorded["arrival_id"], recorded["direction"]
        try:
            photo = self.http.get("/camera/snapshot", params={"camera": role}, timeout=10)
            photo.raise_for_status()
            if not photo.headers.get("content-type", "").startswith("image/jpeg") or not photo.content.startswith(b"\xff\xd8"):
                raise ValueError("Camera did not return a fresh JPEG")
            analyzed = self.http.post("/parking/analyze", data={"camera": role},
                                      files={"image": ("arrival.jpg", photo.content, "image/jpeg")})
            analyzed.raise_for_status()
            observation = analyzed.json()
            body = {"observation_id": observation["observation_id"]}
            if role == "entrance" and self.clock.verified():
                plate = observation.get("license_plate") or ""
                lcd = self.lcd_map.get(plate, plate)
                if 1 <= len(lcd) <= 16 and all(32 <= ord(char) <= 126 for char in lcd):
                    body["lcd_plate"] = lcd
                body["issued_at"] = self.clock().isoformat()
            result = self.post(f"/arrivals/{aid}/prepare", body)
        except (httpx.HTTPError, ValueError, KeyError):
            self.post(f"/arrivals/{aid}/review", {"reason": "capture_or_prepare_failed"})
            raise
        command = result.get("command")
        if command and result["state"] == "reserved" and not result["replayed"]:
            self.pending[aid] = (command, 0.0)
        logger.info("%s %s: %s", role, aid, result["state"])

    def receive(self, topic, payload, retained):
        if retained:
            raise ValueError("Retained gate events are ignored")
        data = decode(payload)
        if topic == ARRIVAL:
            self.arrival(data)
            return
        if topic not in {ACK, FEEDBACK}:
            return
        result = self.post("/gate/ack" if topic == ACK else "/gate/feedback", data)
        pending = self.pending.get(data["arrival_id"])
        if pending and pending[0]["command_id"] == data.get("command_id"):
            self.pending.pop(data["arrival_id"])
        if result["receipt"]:
            self.publish(FEEDBACK_ACK, json.dumps(result["receipt"], separators=(",", ":")))

    def tick(self):
        for aid, (command, last_sent) in list(self.pending.items()):
            if not self.clock.verified() or self.clock() >= datetime.fromisoformat(command["expires_at"]):
                self.post(f"/arrivals/{aid}/review", {"reason": "command_ack_timeout"})
                self.pending.pop(aid)
            elif monotonic() - last_sent >= 1:
                self.publish(COMMAND, json.dumps(command, ensure_ascii=False, separators=(",", ":")))
                self.pending[aid] = (command, monotonic())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensor-repo", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--lcd-map", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("PARKING_PASSAGE_KEY")
    if not key:
        parser.error("Set PARKING_PASSAGE_KEY to the same private value as the API")
    lcd_map = json.loads(args.lcd_map.read_text(encoding="utf-8")) if args.lcd_map else {}
    if not isinstance(lcd_map, dict) or any(not isinstance(value, str) for value in lcd_map.values()):
        parser.error("LCD mapping must contain string values")
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", trust_env=False,
                      headers={"X-Passage-Key": key}, timeout=30) as http:
        worker = PassageWorker(http, None, None, lcd_map)
        worker.preflight()
        print("Passage API ready: isolated SQLite, simulation-only", flush=True)
        if args.check:
            return

        import paho.mqtt.client as mqtt

        clock_path = args.sensor_repo / "backend" / "parking_clock.py"
        spec = importlib.util.spec_from_file_location("sensor_parking_clock", clock_path)
        if spec is None or spec.loader is None:
            parser.error("Sensor checkout with backend/parking_clock.py required")
        clock_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(clock_module)
        stop = Event()
        worker.clock = clock_module.VerifiedClock(stop)
        sensor_env = dotenv_values(args.sensor_repo / ".env")
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="parking-passage-" + uuid4().hex[:12])
        client.username_pw_set(sensor_env["MQTT_USERNAME"], sensor_env["MQTT_PASSWORD"])
        messages = Queue(maxsize=200)

        def connected(client, userdata, flags, reason, properties):
            if reason == 0:
                client.subscribe([(topic, 1) for topic in (ARRIVAL, ACK, FEEDBACK)])

        def message(client, userdata, message):
            try:
                messages.put_nowait((message.topic, bytes(message.payload), message.retain))
            except Full:
                logger.warning("Input queue full. Waiting for the sensor gateway to retry.")

        def publish(topic, body):
            if client.is_connected():
                client.publish(topic, body, qos=1, retain=False)

        worker.publish = publish
        client.on_connect = connected
        client.on_message = message
        worker.post("/worker/recover")
        client.connect(sensor_env["MQTT_BIND_IP"], int(sensor_env.get("MQTT_PORT") or 1883), 15)
        client.loop_start()
        try:
            while True:
                try:
                    try:
                        item = messages.get(timeout=.2)
                    except Empty:
                        item = None
                    if item:
                        worker.receive(*item)
                except (ValueError, KeyError, httpx.HTTPError) as exc:
                    logger.warning("Gate event needs review or retry: %s", type(exc).__name__)
                try:
                    worker.tick()
                except httpx.HTTPError as exc:
                    logger.warning("Command state update will retry: %s", type(exc).__name__)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    main()
