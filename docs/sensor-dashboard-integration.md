# Sensor dashboard integration v1

The camera/decision service is the only parking allocator. When Supabase is configured, it stores parking sessions, five typed spaces, recognition observations, decisions, simulated gate commands, and private photos. The sensor project's InfluxDB continues to store telemetry; it is not a second parking-session writer.

## Space IDs and ownership

| IDs | Allowed vehicles | Capacity |
|---|---|---:|
| `VIP-1`, `VIP-2` | `sportcar`, `supercar` | 2 |
| `NORMAL-1`, `NORMAL-2`, `NORMAL-3` | `car` | 3 |

Gemini classifies at entrance; PaddleOCR reads both directions. Exit uses the entry session's stored type. There is no cross-type overflow. Unknown/low-confidence recognition is denied. A repeated observation returns its original decision without another allocation or OPEN command. A known exit frees its own space, even if the lot is full. An unknown exit cannot release a space.

The current operator flow is: **Simulate sensor alert → analyze/archive the photo → review the reading → Check gate & register entry/exit**. A successful simulated entry allocates a space and counts it as occupied; this is an admission ledger, not proof of physical bay occupancy. A successful simulated exit closes that session. Real passage/servo acknowledgement remains a future integration, and no physical OPEN is published.

## Ready HTTP endpoints

Default camera service: `http://127.0.0.1:8000`. The OpenAPI contract is at `/openapi.json` and `/docs`.

| Method / route | Sends / receives |
|---|---|
| `GET /camera/snapshot?camera=entrance` | Fresh JPEG from the chosen ESP32-CAM. Use `camera=exit` for departure. Camera connectivity failures return an error; an old stored photo is not substituted. |
| `GET /parking/status` | Five bays; type-specific total/occupied/available/unavailable counts; sensor timestamps; storage/backend and simulator mode |
| `GET /parking/history?limit=20` | Recent visits and decisions, UTC entry/exit times, private photo-proxy references |
| `POST /parking/analyze` | Multipart `image`, `camera=entrance|exit`, optional `plate_bbox`; returns server-stored `observation_id` and recognition. Optional `previous_observation_id` reuses the same photo's entrance classification after correcting the crop. |
| `POST /parking/commit` | JSON `observation_id`, stable `request_id`; atomically allocates/releases and returns allow/deny with `gate.mode=simulation` |
| `GET /parking/media/{uuid}` | JPEG through the backend; cloud bucket stays private; no Supabase secret appears in the browser |
| `POST /parking/sensors` | Authenticated, timestamped occupancy observations, described below |

Status reads and gate decisions depend on Supabase. If the cloud cannot confirm a commit, the simulator does not confirm a new OPEN. Retry the same observation/request ID to recover a committed decision after a lost response. Do not generate a new request to retry a timed-out gate action.

For a future arrival/departure alert, the dashboard **backend** can request the corresponding snapshot and send that JPEG to `/parking/analyze` with the same camera direction. Present the resulting plate, classification and observation ID for review, then send that ID to `/parking/commit`. Persist the observation ID against the alert before committing, so a retry cannot create a second capture or visit. Bay occupancy messages are not arrival alerts and must not automatically create or close visits. The current web button already performs the capture/analyze part of this sequence.

The checked-in [OpenAPI snapshot](openapi.json) lists the ready HTTP request fields. The running `/openapi.json` remains authoritative after future code changes. The camera service currently listens on loopback; the sensor PC needs an authenticated tunnel or a same-host deployment before it can call these endpoints.

## Sensor message

The sensor backend sends `X-Integration-Key`, configured as `SENSOR_INTEGRATION_TOKEN` in the camera service's private `.env`. Keep it server-side. No cloud secret or MQTT credential is needed in the dashboard browser.

```json
{
  "schema_version": 1,
  "source": "sensor-dashboard",
  "spaces": [
    {"space_id": "NORMAL-1", "occupied": true, "observed_at": "2026-11-27T00:00:00Z"}
  ]
}
```

Send real current UTC timestamps, not the example timestamp. One batch has 1–5 unique known space IDs and strict Boolean occupancy values. Out-of-order observations cannot overwrite newer ones. Future timestamps over 30 seconds are rejected. Sensor observations expire after 15 seconds. An observed occupied or stale space is excluded from allocation; a sensor reporting free never silently deletes an active parking session.

Set `PARKING_REQUIRE_FRESH_SENSORS=true` before a sensor-controlled deployment: then every allocatable bay needs a fresh free reading. With this false, never-instrumented bays can be exercised through the simulator; once a bay has a sensor record, stale readings do not make it available.

## Adapter for the existing sensor project

`scripts/sync_sensor_dashboard.py` reads the existing authenticated `GET /api/latest` on port 5000 and forwards only `sources.uno` data with `fresh=true`, `payload_valid=true`, a recent server receive timestamp, and a valid Boolean occupancy. It does not modify or restart the teammate's software, broker ACLs or firmware.

The existing `iot-parking-dashboard` model currently supports A1 and marks M1 **not installed**. It does not provide five physical bay sensors. More sensor channels/firmware changes are required for all five bays. Do not invent readings for missing sensors or infer entry/exit from a bay changing state.

Create a local mapping file **after confirming the physical wiring**. For example, if A1 is physically the first normal bay:

```json
{"a1": "NORMAL-1"}
```

```bash
python scripts/sync_sensor_dashboard.py \
  --mapping /absolute/path/to/verified-sensor-map.json \
  --dashboard-url http://127.0.0.1:5000 \
  --dashboard-env /absolute/path/to/iot-parking-dashboard/.env \
  --once
```

Remove `--once` for polling every two seconds. Run both services on one host for loopback integration, or use a trusted authenticated tunnel between the sensor PC and the camera Mac. This task does not expose either service publicly. The mapping is deliberately not auto-selected.

The dashboard can consume `/parking/status` and `/parking/history` from its backend to display availability and visits. For same-origin photo display, proxy the returned `/parking/media/{uuid}` through that authenticated backend. Use stable session IDs and entry/exit timestamps; do not create an independent allocator or infer free capacity from stale/missing telemetry.

## Gate integration boundary

Only the web gate simulator is enabled. The existing sensor team's proposed MQTT topics `parking/v1/arrival`, `parking/v1/gate/command`, and `parking/v1/gate/ack` remain **future interfaces**, not active publish/subscribe paths. Do not grant broker ACLs or move a servo based on this document. Agree command IDs, expiry, acknowledgements, power/wiring and physical passage feedback before enabling a physical gate.

## Setup and checks

1. Apply `migrations/001_parking_supabase.sql` using the existing Supabase project; `002_fix_parking_function_scope.sql` patches the initial migration if it was already applied before the scoped-variable correction.
2. Server-only `.env`: `PARKING_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_STORAGE_BUCKET=intelligence-parking-lot`, and `SENSOR_INTEGRATION_TOKEN`. The bucket is private and the tables/RPCs are service-role-only.
3. Start `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
4. Run `python -m pytest -q` for isolated policy/contract tests. `python scripts/verify_parking_cloud.py` requires an empty simulated lot, creates uniquely identified TEST fixtures, validates concurrent capacity and storage readback, then removes only its own fixture rows/photos.

See the exported system and decision-flow PNG/SVG diagrams in this directory and the verification results in `reports/parking_cloud_verification.json`.

## Verified on November 27, 2026

- 68 automated tests passed, including concurrent local allocation and sensor validation.
- Live Supabase checks filled each category independently, denied overflow into the other category, released the correct bay on exit, verified retry protection and downloaded private test photos. Those generated fixtures were removed.
- Authenticated sensor API checks verified occupied/stale exclusion, ordering protection and strict required-sensor mode; only the generated sensor fixture was removed.
- The real browser test used the supplied `ME204` image: Gemini classified it as `sportcar`, entry allocated `VIP-1`, and local PaddleOCR matched its exit. The saved visit is now `left`; all five spaces are available. Entry/exit photos remain in the private bucket as a reviewable software test.
- No real vehicle passage, physical gate movement or five-sensor hardware connection was tested. Both live camera previews were unavailable during the browser test; the uploaded image exercised the AI/cloud/UI path.

Evidence: `reports/parking_web_verification.json`, `reports/parking_sensor_cloud_verification.json` and `reports/assets/parking-exit-web.jpg`. Rebuild the overview with `python scripts/render_parking_diagrams.py` (requires `rsvg-convert`); rebuild the decision diagram using Graphviz `dot` and its `.dot` source.
