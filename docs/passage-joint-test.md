# Passage-aware parking joint test

This runner pairs with sensor main [`f92ef31`](https://github.com/ananyaknr/iot-parking-dashboard/tree/f92ef31). Run both on the Windows sensor laptop. Keep the SG90 disconnected and use the isolated sensor dashboard on port 5001, a separate parking API on port 8001, and the `parking/test/v1/...` topics. Parking reservations, passages and photos go to SQLite and disk in the test directory. The shared Supabase instance stays outside this run.

## Windows startup

1. Update the camera checkout to the commit that contains this guide. Keep its existing Python environment, local Gemini key and PaddleOCR models. Verify `ESP32_ENTRANCE_CAPTURE_URL` and `ESP32_EXIT_CAPTURE_URL` in the camera checkout's private `.env` against the current camera IPs. Both must be reachable from Windows. Update the sensor checkout to `f92ef31` or a reviewed compatible version.
2. Start the existing isolated sensor services with the sensor repo's `scripts/run-gate-demo.py` if they are stopped. Set `PARKING_API_URL=http://127.0.0.1:8001` and `PARKING_SIMULATOR_COMMIT=no` in the environment used by the sensor dashboard. Stop the old `run-parking-http.py` bridge and any mock acknowledgement writer before starting the new worker. Run one worker because it owns recovery for this test ledger. [Sensor MQTT contract and service instructions](https://github.com/ananyaknr/iot-parking-dashboard/blob/f92ef31/docs/parking-v1-confirmed.md)
3. In PowerShell inside the camera checkout, start the passage API. Use a fresh test directory for a new run and keep the same directory across restarts within that run.

```powershell
$TestRoot = Join-Path $PWD 'data/runtime/passage-joint'
New-Item -ItemType Directory -Force $TestRoot | Out-Null
$KeyFile = Join-Path $TestRoot 'key.txt'
if (-not (Test-Path $KeyFile)) {
    ([guid]::NewGuid().ToString('N')) | Set-Content -Encoding ascii -NoNewline $KeyFile
}
$env:PARKING_PASSAGE_KEY = (Get-Content $KeyFile -Raw).Trim()
$env:PARKING_PASSAGE_MODE = 'true'
$env:PARKING_BACKEND = 'local'
$env:PARKING_DATABASE_PATH = Join-Path $TestRoot 'parking.sqlite3'
$env:PARKING_STORAGE_PATH = Join-Path $TestRoot 'photos'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

4. In a second PowerShell terminal in the camera checkout, verify both live snapshots in the sensor dashboard or through the snapshot API. Start the worker using the sensor repo's broker settings and clock verification. Change `$SensorRepo` if the two checkouts are elsewhere.

```powershell
$SensorRepo = (Resolve-Path '..\iot-parking-dashboard').Path
$env:PARKING_PASSAGE_KEY = (Get-Content 'data/runtime/passage-joint/key.txt' -Raw).Trim()
.\.venv\Scripts\python.exe -m pip install paho-mqtt==2.1.0
.\.venv\Scripts\python.exe scripts/run_passage_worker.py --sensor-repo $SensorRepo --check
.\.venv\Scripts\python.exe scripts/run_passage_worker.py --sensor-repo $SensorRepo
```

For a Unicode plate, add `--lcd-map PATH` to the worker. Supply a private JSON object mapping the exact recognized text to a printable ASCII display label of at most 16 characters. Use a known ASCII test plate to avoid this setup. The worker never changes the original plate match. Before triggering U1, allow the worker to verify UTC through the same SNTP check used by the sensor repo. The host clock and verified UTC must be within one second for issuance.

## HTTP contract

The [OpenAPI specification](passage-openapi.json) includes request and response schemas. The complete running spec is at [`/openapi.json`](http://127.0.0.1:8001/openapi.json). Every route under `/parking/passage` takes `X-Passage-Key` matching the private key on the server. Keep these routes on loopback and out of the dashboard proxy. The worker uses the existing `GET /camera/snapshot` and multipart `POST /parking/analyze` paths. `GET /parking/status` must show `storage:"local"`, `gate_mode:"simulation"`, and `workflow:"passage"`. The legacy `POST /parking/commit` returns 409 in passage mode.

| Method and path under `/parking/passage` | Request | Response and behavior |
|---|---|---|
| GET `/health` | Passage key | Mode and storage. Used by worker preflight. |
| POST `/arrivals` | The complete v1 ultrasonic arrival | Records a new capture claim before requesting a snapshot. Exact retries return `replayed:true`; changed content with the same arrival ID conflicts. New events older than 18 seconds are refused. |
| POST `/arrivals/{arrival_id}/prepare` | `observation_id` from analysis. An entrance adds `lcd_plate` and verified UTC `issued_at`. | First eligible entrance call atomically reserves a matching bay and returns one command payload. Exact observation replays never authorize another publication. Exit binds the session active at the arrival time and returns no OPEN. |
| POST `/gate/ack` | Correlated v1 command ACK from the sensor gateway | Records the ACK and returns `{"receipt":null}`. Accepted leaves the bay reserved. An unambiguous pre-opening rejection cancels the reservation. Unknown, failed, and contradictory outcomes retain the hold for review. |
| POST `/gate/feedback` | The original v1 feedback with `simulation:true` | Saves it durably and returns `{"receipt":{...}}`, the exact v1 `gate/feedback/ack` payload. Repeated event IDs return the original receipt if the payload matches. Completed confirmed feedback finalizes only its matching reservation or exit session. Feedback may arrive before the snapshot and match. |
| GET `/arrivals/{arrival_id}` | Passage key | Current arrival state, `session_id`, `space_id`, `observation_id`, `decision_id`, `ack_state` and `reason`. The returned `command` is null on a status read. |
| POST `/arrivals/{arrival_id}/review` | `reason` of `capture_or_prepare_failed` or `command_ack_timeout` | Marks uncertain work for review and keeps any reserved bay unavailable. |
| POST `/arrivals/{arrival_id}/cancel` | `{"confirmed_no_passage":true}` after supervised reconciliation | Releases an unresolved entrance reservation after the original command expires plus one second of clock margin. Refuses cancellation of a completed visit. Late contradictory proof remains recorded for review and cannot create a visit from a cancelled reservation. |
| POST `/worker/recover` | Passage key | Marks interrupted captures and unacknowledged reservations for review. The worker invokes it before connecting. Holds remain until correlated feedback or supervised reconciliation. |

Errors use the FastAPI `detail` field. Missing/wrong credentials return 401, unknown arrival or observation returns 404, conflicts return 409, invalid payloads return 422 and SQLite failures return 503.

An entrance command lasts exactly five seconds and must fit inside the 20-second entrance window. The worker retries it once per second using the original payload and stops on expiry, a correlated response or loss of verified time. It does not reconstruct an OPEN after restarting. The sensor gateway retains feedback until it gets the application receipt, then retries on its existing 2-to-30-second schedule if delivery is unconfirmed. A receipt means the parking service has saved the feedback. A matching analysis may still be pending.

The dashboard can distinguish `reserved` and `review` bays from `occupied`. Only completed entrance passages create `still_in` visits. Completed exit passages update the exact matched session and enter the shared history view only once. If capture fails or recognition is inconclusive, inspect the arrival state before proceeding.

## Three joint acceptance tests

| Test | Required evidence |
|---|---|
| **1. Entrance** | Trigger U1 with the labeled test vehicle in the entrance view. Confirm the automatic live-camera analysis, correct bay type, reserved bay and one entrance OPEN. An accepted command must leave visits unchanged. After supervised passage confirmation and the sensor team's close sequence, verify one active session with the command's original session ID. |
| **2. Duplicate entrance** | Replay the identical original arrival through the isolated test publisher, or observe its original retry. Verify the same arrival, observation and session, one allocation and no second controller cycle. If retransmission is needed, compare command IDs rather than the number of network deliveries. |
| **3. Matched exit** | Present the same vehicle to the exit camera and trigger U2. Verify its local cycle and matching original session ID, with the bay still occupied before passage confirmation. After supervised confirmed feedback, verify the original session becomes `left` and its bay becomes free. The worker sends no exit OPEN. |

Use those three tests for hardware acceptance. The local focused tests in `tests/test_passage_integration.py` exercise the actual API and worker with a fixture JPEG and in-process message delivery. Hardware acceptance requires the live camera and controller evidence above.
