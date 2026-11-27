from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .ocr_reader import normalize_plate_text

ROOT = Path(__file__).resolve().parents[1]
SPACES = (("VIP-1", "vip"), ("VIP-2", "vip"), ("NORMAL-1", "normal"), ("NORMAL-2", "normal"), ("NORMAL-3", "normal"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_plate(text: str | None) -> str | None:
    plate = normalize_plate_text(text or "")
    return plate.upper() if plate and len(plate) <= 32 else None


def space_kind(vehicle_type: str | None) -> str | None:
    if vehicle_type == "car":
        return "normal"
    if vehicle_type in {"sportcar", "supercar"}:
        return "vip"
    return None


class ParkingStore:
    """Durable local ledger. One write transaction decides capacity and admission."""

    passage_mode = False

    def __init__(self, database: Path | None = None, storage: Path | None = None):
        self.database = Path(database or os.getenv("PARKING_DATABASE_PATH", str(ROOT / "data/runtime/parking.sqlite3"))).resolve()
        self.storage = Path(storage or os.getenv("PARKING_STORAGE_PATH", str(ROOT / "data/storage/photos"))).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.storage.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if not self.passage_mode and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='passage_arrivals'"
            ).fetchone():
                raise ValueError("This ledger must be opened in passage mode")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('vip','normal'))
                );
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY, filename TEXT UNIQUE NOT NULL,
                    bytes INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, camera TEXT NOT NULL CHECK(camera IN ('entrance','exit')),
                    fingerprint TEXT NOT NULL, photo_id TEXT NOT NULL REFERENCES media(id),
                    crop_id TEXT REFERENCES media(id), recognition TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, plate TEXT NOT NULL,
                    vehicle_type TEXT NOT NULL CHECK(vehicle_type IN ('car','sportcar','supercar')),
                    space_id TEXT NOT NULL REFERENCES spaces(id),
                    status TEXT NOT NULL CHECK(status IN ('still_in','left')),
                    entered_at TEXT NOT NULL, exited_at TEXT,
                    entrance_observation_id TEXT NOT NULL REFERENCES observations(id),
                    exit_observation_id TEXT REFERENCES observations(id),
                    CHECK((status='still_in' AND exited_at IS NULL) OR (status='left' AND exited_at IS NOT NULL))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_plate ON sessions(plate) WHERE status='still_in';
                CREATE UNIQUE INDEX IF NOT EXISTS active_space ON sessions(space_id) WHERE status='still_in';
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, observation_id TEXT UNIQUE NOT NULL REFERENCES observations(id),
                    request_id TEXT UNIQUE NOT NULL, camera TEXT NOT NULL,
                    plate TEXT, vehicle_type TEXT, space_id TEXT REFERENCES spaces(id),
                    session_id TEXT REFERENCES sessions(id), approved INTEGER NOT NULL CHECK(approved IN (0,1)),
                    reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gate_commands (
                    id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL REFERENCES events(id),
                    camera TEXT NOT NULL, command TEXT NOT NULL CHECK(command='OPEN'),
                    mode TEXT NOT NULL CHECK(mode='simulation'), created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS enforce_matching_space BEFORE INSERT ON sessions
                WHEN NOT EXISTS (
                    SELECT 1 FROM spaces WHERE id=NEW.space_id AND
                    ((kind='normal' AND NEW.vehicle_type='car') OR
                     (kind='vip' AND NEW.vehicle_type IN ('sportcar','supercar')))
                ) BEGIN SELECT RAISE(ABORT, 'vehicle and space types do not match'); END;
            """)
            db.executemany("INSERT OR IGNORE INTO spaces(id,kind) VALUES (?,?)", SPACES)
            db.commit()
            actual = {(row["id"], row["kind"]) for row in db.execute("SELECT id,kind FROM spaces")}
            if actual != set(SPACES):
                raise RuntimeError("Parking database does not match the configured 2 VIP / 3 normal spaces")
        # Verify the archive is writable before reporting it as initialized.
        with tempfile.TemporaryFile(dir=self.storage) as probe:
            probe.write(b"parking-storage-check")
            probe.flush()
            os.fsync(probe.fileno())
            probe.seek(0)
            if probe.read() != b"parking-storage-check":
                raise OSError("Photo archive readback failed")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _write_media(self, data: bytes) -> tuple[str, Path]:
        media_id = str(uuid4())
        path = self.storage / f"{media_id}.jpg"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.storage, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            return media_id, path
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_observation(self, camera: str, fingerprint: str, photo: bytes, crop: bytes | None, recognition: dict) -> dict:
        if camera not in {"entrance", "exit"}:
            raise ValueError("Unsupported camera")
        created = utc_now()
        media = []
        try:
            photo_id, photo_path = self._write_media(photo)
            media.append((photo_id, photo_path, len(photo)))
            crop_id = None
            if crop is not None:
                crop_id, crop_path = self._write_media(crop)
                media.append((crop_id, crop_path, len(crop)))
            observation_id = str(uuid4())
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.executemany("INSERT INTO media VALUES (?,?,?,?)", [(key, path.name, size, created) for key, path, size in media])
                db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", (
                    observation_id, camera, fingerprint, photo_id, crop_id,
                    json.dumps(recognition, ensure_ascii=False), created,
                ))
                db.commit()
            return self.observation(observation_id)
        except Exception:
            for _, path, _ in media:
                path.unlink(missing_ok=True)
            raise

    def observation(self, observation_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
        if row is None:
            raise KeyError("Observation not found")
        result = dict(row)
        result["recognition"] = json.loads(result["recognition"])
        result["photo_url"] = f"/parking/media/{result['photo_id']}"
        result["crop_url"] = f"/parking/media/{result['crop_id']}" if result["crop_id"] else None
        return result

    def media_path(self, media_id: str) -> Path:
        with self.connection() as db:
            row = db.execute("SELECT filename FROM media WHERE id=?", (media_id,)).fetchone()
        if row is None:
            raise KeyError("Photo not found")
        path = self.storage / row["filename"]
        if not path.is_file():
            raise FileNotFoundError("Archived photo is missing")
        return path

    @staticmethod
    def _decision(row, replayed: bool = False) -> dict:
        result = dict(row)
        result["approved"] = bool(result["approved"])
        result["gate"] = {
            "mode": "simulation", "action": "open" if result["approved"] else "keep_closed",
            "command_created": bool(result["approved"]) and not replayed,
            "physical_gate_opened": False,
        }
        result["replayed"] = replayed
        return result

    def commit(self, observation_id: str, request_id: str) -> dict:
        with self.connection() as db:
            # Capacity selection, allocation/release, audit record and gate command
            # commit together. Concurrent requests cannot oversubscribe a space.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM events WHERE observation_id=? OR request_id=?", (observation_id, request_id)).fetchone()
            if existing:
                if existing["observation_id"] != observation_id:
                    raise ValueError("Request ID was already used for a different observation")
                return self._decision(existing, replayed=True)
            obs = db.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
            if obs is None:
                raise KeyError("Observation not found")
            recognition = json.loads(obs["recognition"])
            camera = obs["camera"]
            plate = canonical_plate(recognition.get("license_plate"))
            vehicle = recognition.get("vehicle_type")
            approved, reason, space_id, session_id = False, "recognition_needs_review", None, None
            now = utc_now()
            if recognition.get("eligible") is True and plate:
                active = db.execute("SELECT * FROM sessions WHERE plate=? AND status='still_in'", (plate,)).fetchone()
                if camera == "exit":
                    if active is None:
                        reason = "no_active_session"
                    else:
                        session_id, space_id, vehicle = active["id"], active["space_id"], active["vehicle_type"]
                        db.execute("UPDATE sessions SET status='left', exited_at=?, exit_observation_id=? WHERE id=?", (now, observation_id, session_id))
                        approved, reason = True, "exit_registered"
                elif active:
                    session_id, space_id = active["id"], active["space_id"]
                    reason = "already_inside"
                else:
                    kind = space_kind(vehicle)
                    if kind is None:
                        reason = "unsupported_vehicle_type"
                    else:
                        bay = db.execute("""SELECT b.id FROM spaces b WHERE b.kind=? AND NOT EXISTS
                            (SELECT 1 FROM sessions s WHERE s.space_id=b.id AND s.status='still_in')
                            ORDER BY b.id LIMIT 1""", (kind,)).fetchone()
                        if bay is None:
                            reason = f"{kind}_full"
                        else:
                            session_id, space_id = str(uuid4()), bay["id"]
                            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", (
                                session_id, plate, vehicle, space_id, "still_in", now, None, observation_id, None,
                            ))
                            approved, reason = True, "entry_registered"
            event_id = str(uuid4())
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                event_id, observation_id, request_id, camera, plate, vehicle, space_id, session_id, int(approved), reason, now,
            ))
            if approved:
                db.execute("INSERT INTO gate_commands VALUES (?,?,?,?,?,?)", (str(uuid4()), event_id, camera, "OPEN", "simulation", now))
            db.commit()
            return self._decision(db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())

    def status(self) -> dict:
        with self.connection() as db:
            return self._status(db)

    @staticmethod
    def _status(db) -> dict:
        spaces = [dict(row) for row in db.execute("""SELECT b.id,b.kind,s.id session_id,s.plate,s.vehicle_type,s.entered_at
            FROM spaces b LEFT JOIN sessions s ON s.space_id=b.id AND s.status='still_in'
            ORDER BY CASE b.kind WHEN 'vip' THEN 0 ELSE 1 END,b.id""")]
        observations = db.execute("SELECT count(*) FROM observations").fetchone()[0]
        media_count = db.execute("SELECT count(*) FROM media").fetchone()[0]
        for bay in spaces:
            bay["status"] = "occupied" if bay["session_id"] else "free"
        capacity = {}
        for kind in ("vip", "normal"):
            group = [bay for bay in spaces if bay["kind"] == kind]
            occupied = sum(bay["status"] == "occupied" for bay in group)
            capacity[kind] = {"total": len(group), "occupied": occupied, "available": len(group) - occupied}
        return {"capacity": capacity, "spaces": spaces, "database": "ready", "storage": "local",
                "archived_observations": observations, "archived_images": media_count,
                "gate_mode": "simulation", "occupancy_source": "registered_entries_and_exits"}

    def history(self, limit: int = 30) -> dict:
        with self.connection() as db:
            events = [dict(row) for row in db.execute("""SELECT e.*,o.photo_id,o.crop_id FROM events e
                JOIN observations o ON o.id=e.observation_id ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?""", (limit,))]
            sessions = [dict(row) for row in db.execute("""SELECT s.*,o.photo_id entrance_photo_id,x.photo_id exit_photo_id
                FROM sessions s JOIN observations o ON o.id=s.entrance_observation_id
                LEFT JOIN observations x ON x.id=s.exit_observation_id
                ORDER BY s.entered_at DESC,s.rowid DESC LIMIT ?""", (limit,))]
        for event in events:
            event["approved"] = bool(event["approved"])
            event["photo_url"] = f"/parking/media/{event['photo_id']}"
        for session in sessions:
            session["entrance_photo_url"] = f"/parking/media/{session['entrance_photo_id']}"
            session["exit_photo_url"] = f"/parking/media/{session['exit_photo_id']}" if session["exit_photo_id"] else None
        return {"events": events, "sessions": sessions}
