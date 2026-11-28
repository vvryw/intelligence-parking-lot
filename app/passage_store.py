"""Passage-aware parking ledger for isolated MQTT tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .parking_store import ParkingStore, canonical_plate, space_kind, utc_now

SIMULATION = "simulation"
PHYSICAL_INFERRED_TEST = "physical_inferred_test"


def encode(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class PassageParkingStore(ParkingStore):
    passage_mode = True

    def __init__(self, database, storage, gate_mode=SIMULATION):
        if gate_mode not in {SIMULATION, PHYSICAL_INFERRED_TEST}:
            raise ValueError("Unknown passage gate mode")
        self.gate_mode = gate_mode
        super().__init__(database, storage)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            initialized = db.execute("SELECT 1 FROM sqlite_master WHERE name='passage_arrivals'").fetchone()
            if not initialized and db.execute("SELECT 1 FROM observations LIMIT 1").fetchone():
                raise ValueError("Passage mode requires a separate test ledger")
            db.execute("""CREATE TABLE IF NOT EXISTS passage_mode_binding (
                id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL)""")
            bound = db.execute("SELECT mode FROM passage_mode_binding WHERE id=1").fetchone()
            if (bound and bound["mode"] != gate_mode) or (not bound and initialized and not self.simulation):
                raise ValueError("Passage ledger mode mismatch. Use a separate test ledger.")
            if not bound:
                db.execute("INSERT INTO passage_mode_binding VALUES (1,?)", (gate_mode,))
            if initialized:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(passage_arrivals)")}
                if "passage_basis" not in columns:
                    db.execute("ALTER TABLE passage_arrivals ADD COLUMN passage_basis TEXT")
                if "completion_feedback_id" not in columns:
                    db.execute("ALTER TABLE passage_arrivals ADD COLUMN completion_feedback_id TEXT")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS passage_arrivals (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, direction TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, decision_id TEXT NOT NULL,
                    observation_id TEXT UNIQUE REFERENCES observations(id),
                    session_id TEXT, space_id TEXT REFERENCES spaces(id),
                    plate TEXT, vehicle_type TEXT, command TEXT,
                    ack_state TEXT, state TEXT NOT NULL, reason TEXT,
                    passage_basis TEXT, completion_feedback_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS passage_arrival_event
                    ON passage_arrivals(json_extract(payload, '$.event_id'));
                CREATE UNIQUE INDEX IF NOT EXISTS passage_held_space
                    ON passage_arrivals(space_id)
                    WHERE direction='entrance' AND state IN ('reserved','review');
                CREATE UNIQUE INDEX IF NOT EXISTS passage_held_plate
                    ON passage_arrivals(plate)
                    WHERE direction='entrance' AND state IN ('reserved','review') AND space_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS passage_receipts (
                    event_id TEXT PRIMARY KEY, arrival_id TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, receipt TEXT
                );
                CREATE INDEX IF NOT EXISTS passage_receipts_arrival
                    ON passage_receipts(arrival_id,kind);
            """)

    @property
    def simulation(self):
        return self.gate_mode == SIMULATION

    def require_mode(self, payload):
        if payload.get("simulation", True) is not self.simulation:
            raise ValueError("MQTT event mode does not match the passage ledger")

    def commit(self, observation_id, request_id):
        raise ValueError("Use the passage workflow. Direct parking commits are disabled in this test instance.")

    @staticmethod
    def _row(db, arrival_id):
        row = db.execute("SELECT * FROM passage_arrivals WHERE id=?", (arrival_id,)).fetchone()
        if row is None:
            raise KeyError("Arrival not found")
        return row

    @classmethod
    def _view(cls, db, arrival_id, *, replayed=False):
        row = cls._row(db, arrival_id)
        fields = ("direction", "decision_id", "observation_id", "session_id", "space_id",
                  "plate", "vehicle_type", "ack_state", "state", "reason", "passage_basis", "completion_feedback_id")
        return {"arrival_id": row["id"], **{field: row[field] for field in fields},
                "replayed": replayed, "command": None}

    def arrival(self, payload):
        self.require_mode(payload)
        aid, raw = payload["arrival_id"], encode(payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM passage_arrivals WHERE id=?", (aid,)).fetchone()
            if old:
                if old["payload"] != raw:
                    raise ValueError("Conflicting arrival ID")
                return self._view(db, aid, replayed=True)

            occurred_at = datetime.fromisoformat(payload["occurred_at"])
            age = (datetime.now(timezone.utc) - occurred_at).total_seconds()
            if not -1 <= age < 18:
                raise ValueError("Arrival is outside the capture window")
            db.execute("""INSERT INTO passage_arrivals
                (id,payload,direction,occurred_at,decision_id,state) VALUES (?,?,?,?,?,'capturing')""",
                (aid, raw, payload["direction"], payload["occurred_at"], str(uuid4())))
            db.commit()
            return self._view(db, aid)

    def get_arrival(self, arrival_id):
        with self.connection() as db:
            return self._view(db, arrival_id)

    def prepare(self, arrival_id, observation_id, lcd_plate, issued_at):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, arrival_id)
            if row["observation_id"] is not None:
                if row["observation_id"] != observation_id:
                    raise ValueError("Arrival already has a different observation")
                return self._view(db, arrival_id, replayed=True)
            if row["state"] != "capturing":
                raise ValueError("Arrival requires review before more work")
            obs = db.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
            if obs is None:
                raise KeyError("Observation not found")
            if obs["camera"] != row["direction"] or obs["created_at"] < row["occurred_at"]:
                raise ValueError("Observation must be a fresh capture for the arrival direction")

            recognition = json.loads(obs["recognition"])
            plate = canonical_plate(recognition.get("license_plate"))
            vehicle_type = recognition.get("vehicle_type")
            session = space = command = None
            state, reason = "review", "recognition_needs_review"

            if recognition.get("eligible") is True and plate:
                active = db.execute("SELECT * FROM sessions WHERE plate=? AND status='still_in'", (plate,)).fetchone()
                if row["direction"] == "exit":
                    if active and active["entered_at"] <= row["occurred_at"]:
                        session, space, vehicle_type = active["id"], active["space_id"], active["vehicle_type"]
                        state, reason = "matched", None
                    else:
                        reason = "no_active_session_at_arrival"
                else:
                    state, reason, space = self._reserve(db, row, plate, vehicle_type, active, lcd_plate, issued_at)
                    if space:
                        session = str(uuid4())
                        command = {
                            "schema_version": 1, "event_id": uuid4().hex, "producer_id": "parking-passage-test",
                            "occurred_at": issued_at.isoformat(), "arrival_id": arrival_id, "command_id": uuid4().hex,
                            "session_id": session, "decision_id": row["decision_id"], "direction": "entrance",
                            "action": "OPEN", "issued_at": issued_at.isoformat(),
                            "expires_at": (issued_at + timedelta(seconds=5)).isoformat(),
                            "license_plate": plate, "lcd_plate": lcd_plate, "vehicle_type": vehicle_type,
                        }
                        if not self.simulation:
                            command["simulation"] = False

            db.execute("""UPDATE passage_arrivals SET observation_id=?, session_id=?, space_id=?,
                plate=?, vehicle_type=?, command=?, state=?, reason=? WHERE id=?""",
                (observation_id, session, space, plate, vehicle_type, encode(command) if command else None,
                 state, reason, arrival_id))
            self._apply_feedback(db, arrival_id)
            result = self._view(db, arrival_id)
            if result["state"] == "reserved":
                result["command"] = command
            db.commit()
            return result

    @staticmethod
    def _reserve(db, row, plate, vehicle_type, active, lcd_plate, issued_at):
        kind = space_kind(vehicle_type)
        if not kind:
            return "review", "unsupported_vehicle_type", None
        if not lcd_plate:
            return "review", "lcd_mapping_required", None
        now = datetime.now(timezone.utc)
        arrival = datetime.fromisoformat(row["occurred_at"])
        if (not issued_at or abs((now - issued_at).total_seconds()) > 1
                or not 0 <= (issued_at - arrival).total_seconds() < 15):
            return "review", "entrance_authorization_window_elapsed_or_unverified", None
        held = db.execute("""SELECT 1 FROM passage_arrivals WHERE direction='entrance'
            AND state IN ('reserved','review') AND plate=? AND space_id IS NOT NULL""", (plate,)).fetchone()
        if active or held:
            return "denied", "plate_already_inside_or_reserved", None
        bay = db.execute("""SELECT b.id FROM spaces b WHERE kind=?
            AND NOT EXISTS (SELECT 1 FROM sessions WHERE space_id=b.id AND status='still_in')
            AND NOT EXISTS (SELECT 1 FROM passage_arrivals WHERE space_id=b.id
                            AND direction='entrance' AND state IN ('reserved','review'))
            ORDER BY id LIMIT 1""", (kind,)).fetchone()
        if bay is None:
            return "denied", f"{kind}_full", None
        return "reserved", None, bay["id"]

    def receive(self, kind, payload):
        self.require_mode(payload)
        aid, event_id, raw = payload["arrival_id"], payload["event_id"], encode(payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM passage_receipts WHERE event_id=?", (event_id,)).fetchone()
            if previous:
                if previous["kind"] != kind or previous["payload"] != raw:
                    raise ValueError("Conflicting gate event ID")
                return {"receipt": json.loads(previous["receipt"]) if previous["receipt"] else None}

            arrival = db.execute("SELECT * FROM passage_arrivals WHERE id=?", (aid,)).fetchone()
            if kind == "ack" and (arrival is None or not self._correlates(arrival, payload, kind)):
                raise ValueError("Acknowledgement does not match an entrance command")
            if arrival and kind == "feedback":
                if payload["direction"] != arrival["direction"]:
                    raise ValueError("Feedback direction does not match the arrival")
                if payload["command_id"] and arrival["command"] and not self._correlates(arrival, payload, kind):
                    raise ValueError("Feedback does not match the arrival and command")

            receipt = None
            if kind == "feedback":
                receipt = {"schema_version": 1, "event_id": uuid4().hex, "producer_id": "parking-passage-test",
                           "occurred_at": utc_now(), "arrival_id": aid, "feedback_id": event_id, "state": "recorded"}
            db.execute("INSERT INTO passage_receipts VALUES (?,?,?,?,?)",
                       (event_id, aid, kind, raw, encode(receipt) if receipt else None))
            if arrival:
                self._apply_feedback(db, aid)
            db.commit()
            return {"receipt": receipt}

    @staticmethod
    def _correlates(row, message, kind):
        if message["occurred_at"] < row["occurred_at"]:
            return False
        if row["direction"] == "exit":
            return kind == "feedback" and message["direction"] == "exit" and message["command_id"] is None
        if kind == "feedback" and message["command_id"] is None:
            return message["direction"] == "entrance" and message["state"] in {"hold", "failed"}
        command = json.loads(row["command"]) if row["command"] else None
        return bool(command and message.get("direction", "entrance") == "entrance"
                    and message["command_id"] == command["command_id"]
                    and message["occurred_at"] >= command["issued_at"])

    def _apply_feedback(self, db, arrival_id):
        row = self._row(db, arrival_id)
        if row["session_id"] is None or row["state"] == "completed":
            return
        messages = [(record["kind"], json.loads(record["payload"])) for record in db.execute(
            "SELECT kind,payload FROM passage_receipts WHERE arrival_id=?", (arrival_id,))]
        valid = [(kind, message) for kind, message in messages if self._correlates(row, message, kind)]
        basis = "supervised_simulation" if self.simulation else "inferred_clearance"
        proof = next((m for kind, m in valid if kind == "feedback" and m["state"] == "completed"
                      and (m["passage_confirmed"] if self.simulation else
                           m.get("passage_basis") == basis and not m["passage_confirmed"])), None)
        if row["state"] == "cancelled":
            if proof:
                db.execute("UPDATE passage_arrivals SET reason='late_confirmation_after_cancellation' WHERE id=?",
                           (arrival_id,))
            return
        if not self.simulation:
            uncertain = any(
                (kind == "ack" and m["state"] in {"unknown", "failed"}) or
                (kind == "feedback" and (m["state"] in {"hold", "failed"} or
                 (m["state"] == "completed" and m.get("passage_basis") != basis))) or
                (proof is not None and kind == "ack" and m["state"] == "rejected")
                for kind, m in valid
            )
            if uncertain or row["state"] == "review":
                if uncertain and row["state"] != "review":
                    db.execute("UPDATE passage_arrivals SET state='review',reason='gate_outcome_uncertain' WHERE id=?",
                               (arrival_id,))
                return
        if proof:
            self._complete(db, row, proof, basis)
        elif row["direction"] == "entrance":
            acknowledgements = {m["state"] for kind, m in valid if kind == "ack"}
            movement = any(kind == "feedback" and m["state"] in {"opening", "completed"} for kind, m in valid)
            feedback_uncertain = any(kind == "feedback" and m["state"] in {"hold", "failed", "completed"}
                                     for kind, m in valid)
            if acknowledgements == {"rejected"} and not movement and not feedback_uncertain:
                db.execute("UPDATE passage_arrivals SET state='cancelled',reason='rejected_before_movement',ack_state='rejected' WHERE id=?",
                           (arrival_id,))
            elif acknowledgements & {"rejected", "unknown", "failed"} or feedback_uncertain:
                db.execute("UPDATE passage_arrivals SET state='review',reason='gate_outcome_uncertain' WHERE id=?", (arrival_id,))
            if "accepted" in acknowledgements:
                db.execute("UPDATE passage_arrivals SET ack_state='accepted' WHERE id=?", (arrival_id,))

    @staticmethod
    def _complete(db, row, proof, passage_basis):
        passed_at = proof["occurred_at"]
        session_id = row["session_id"]
        if row["direction"] == "entrance":
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", (
                session_id, row["plate"], row["vehicle_type"], row["space_id"], "still_in", passed_at,
                None, row["observation_id"], None))
            reason = "entry_registered"
        else:
            changed = db.execute("""UPDATE sessions SET status='left',exited_at=?,exit_observation_id=?
                WHERE id=? AND status='still_in' AND space_id=? AND entered_at<=?""",
                (passed_at, row["observation_id"], session_id, row["space_id"], row["occurred_at"])).rowcount
            if not changed:
                db.execute("UPDATE passage_arrivals SET state='review',reason='matched_session_no_longer_active' WHERE id=?",
                           (row["id"],))
                return
            reason = "exit_registered"
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            row["decision_id"], row["observation_id"], f"passage:{row['id']}", row["direction"], row["plate"],
            row["vehicle_type"], row["space_id"], session_id, 1, reason, passed_at))
        db.execute("""UPDATE passage_arrivals SET state='completed',reason=?,passage_basis=?,
            completion_feedback_id=? WHERE id=?""", (reason, passage_basis, proof["event_id"], row["id"]))

    def review(self, arrival_id, reason):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, arrival_id)
            db.execute("""UPDATE passage_arrivals SET state='review',reason=?
                WHERE id=? AND state IN ('reserved','capturing') AND ack_state IS NULL""", (reason, arrival_id))
            db.commit()
            return self._view(db, arrival_id)

    def recover_worker(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE passage_arrivals SET state='review', reason='worker_restarted'
                WHERE state='capturing' OR (state='reserved' AND ack_state IS NULL)
                   OR (? = 0 AND state IN ('reserved','matched'))""", (self.simulation,)).rowcount
            db.commit()
            return {"review_count": changed}

    def cancel(self, arrival_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, arrival_id)
            if row["state"] == "cancelled":
                return self._view(db, arrival_id, replayed=True)
            if row["direction"] != "entrance" or row["state"] not in {"review", "reserved"} or not row["command"]:
                raise ValueError("No unresolved entrance reservation")
            expiry = datetime.fromisoformat(json.loads(row["command"])["expires_at"])
            if datetime.now(timezone.utc) < expiry + timedelta(seconds=1):
                raise ValueError("The command can still execute. Wait for expiry before rejecting it by review.")
            db.execute("UPDATE passage_arrivals SET state='cancelled',reason='operator_confirmed_no_passage' WHERE id=?",
                       (arrival_id,))
            db.commit()
            return self._view(db, arrival_id)

    def status(self):
        # Single read transaction keeps held bays and active sessions from
        # being counted as free during a concurrent passage update.
        with self.connection() as db:
            db.execute("BEGIN")
            status = self._status(db)
            held = {row["space_id"]: row for row in db.execute("""SELECT space_id,plate,vehicle_type,state
                FROM passage_arrivals WHERE direction='entrance' AND state IN ('reserved','review')
                AND space_id IS NOT NULL""")}
        for bay in status["spaces"]:
            reservation = held.get(bay["id"])
            if reservation and bay["status"] == "free":
                bay.update(status=reservation["state"], plate=reservation["plate"], vehicle_type=reservation["vehicle_type"])
                counts = status["capacity"][bay["kind"]]
                counts["available"] -= 1
                counts["reserved"] = counts.get("reserved", 0) + 1
        source = "confirmed_passages" if self.simulation else "inferred_clearance"
        status.update(workflow="passage", gate_mode=self.gate_mode,
                      occupancy_source=f"{source}_and_entrance_reservations")
        return status

    def history(self, limit=30):
        history = super().history(limit)
        session_ids = [session["id"] for session in history["sessions"]]
        event_ids = [event["id"] for event in history["events"]]
        if not session_ids and not event_ids:
            return history
        with self.connection() as db:
            rows = db.execute(f"""SELECT decision_id,session_id,direction,passage_basis,completion_feedback_id
                FROM passage_arrivals WHERE state='completed'
                AND (session_id IN ({','.join('?' for _ in session_ids) or 'NULL'})
                OR decision_id IN ({','.join('?' for _ in event_ids) or 'NULL'}))""", (*session_ids, *event_ids))
            by_event = {row["decision_id"]: dict(row) for row in rows}
        by_visit = {(item["session_id"], item["direction"]): item for item in by_event.values()}
        for session in history["sessions"]:
            for direction, label in (("entrance", "entry"), ("exit", "exit")):
                completed = by_visit.get((session["id"], direction))
                if completed:
                    session[f"{label}_passage_basis"] = completed["passage_basis"]
                    session[f"{label}_feedback_id"] = completed["completion_feedback_id"]
        for event in history["events"]:
            completed = by_event.get(event["id"])
            if completed:
                event.update(passage_basis=completed["passage_basis"], feedback_id=completed["completion_feedback_id"])
        return history
