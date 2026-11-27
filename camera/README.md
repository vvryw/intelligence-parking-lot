# ESP32-CAM still capture setup

## Entrance and exit boards

| Role | Identify signal |
|---|---|
| Entrance · Coming in | One short white flash |
| Exit · Going out | Two short white flashes |

Label each board and note its USB serial port. To set the role in the firmware
before flashing, add `#define CAMERA_ROLE "entrance"` or
`#define CAMERA_ROLE "exit"` to the local `wifi_secrets.h`. Role firmware reports
`role` and `mac` through `/health`. Send `POST /identify` to the camera or send
`identify` followed by a newline over USB serial at 115200 baud to flash its
identification signal.

Configure each camera with its own serial port:

```bash
python -m app.camera_setup --camera entrance --port ENTRANCE_SERIAL_PORT
python -m app.camera_setup --camera exit --port EXIT_SERIAL_PORT
```

Each command verifies a photo before saving its role-specific capture URL to
`.env`. Restart the web server after changing these addresses. The page's
**Camera direction** selector switches between Entrance and Exit. Captures and
OCR results retain the role of the selected camera.

For a controller, `POST /capture` accepts a form field `camera=entrance` or
`camera=exit`. The default is Entrance. CLI capture supports
`--camera entrance --camera-url` or `--camera exit --camera-url`.

For Entrance, the laptop sends the photo to Gemini for vehicle classification and
a plate box, then reads the crop locally with PaddleOCR. For Exit, Gemini finds a
plate box and PaddleOCR reads the crop locally. A local detector can substitute for
Gemini's exit box. If the crop is unclear, draw a box on the still
or supply `plate_bbox` to the API or `--plate-bbox` to the CLI.

An ESP32-CAM-MB is the USB programmer/power adapter underneath the camera board.
Use USB to upload firmware and read the serial log. Once Wi-Fi firmware is
installed, the camera can boot independently on power and serve images over Wi-Fi;
the computer does not need to remain connected by USB. Gemini and PaddleOCR still
run on the laptop/server. Internet access from another location requires an
additional remote connection or server upload design; this `/capture` workflow
expects a network-reachable camera address.

## Included Wi-Fi firmware (AI Thinker-compatible ESP32-CAM)

[`parking_camera/parking_camera.ino`](parking_camera/parking_camera.ino) provides
`GET /capture` (JPEG), `GET /health`, and Wi-Fi reconnection. It prints
`ESP32_CAPTURE_URL=http://.../capture` at 115200 baud after connecting. It uses the
AI Thinker OV2640 pin map from Espressif's camera example; confirm your camera
board is compatible. The MB adapter's name alone does not define the camera pinout.

1. Copy `parking_camera/wifi_secrets.h.example` to `parking_camera/wifi_secrets.h`
   if the local file does not already exist. Fill in the Wi-Fi network name and
   password locally. Use 2.4 GHz Wi-Fi; the file is excluded from Git.
2. In Arduino IDE, install Espressif's ESP32 board package, open the `.ino`, select
   **AI Thinker ESP32-CAM**, and choose the MB adapter's USB serial port.
3. Upload, then reset the board into normal boot mode. If the adapter cannot
   automatically enter upload mode, follow its boot/reset button procedure.
4. Read the capture URL from the serial monitor and put it in the Python
   project's `.env` as `ESP32_CAPTURE_URL`. Keep `GEMINI_API_KEY` there too for
   vehicle classification.
5. Run the capture-only command below, then inference. With PSRAM the sketch
   captures SVGA JPEGs; it falls back to VGA without PSRAM.

Wi-Fi credentials are compiled into the firmware. Keep build binaries and flash
backups local. The sketch serves unauthenticated HTTP on the local network; a
remote deployment needs a separate authenticated connection design.

## Alternative: Espressif CameraWebServer

1. In Arduino IDE install the Espressif ESP32 board package and select the exact
   ESP32-CAM board/module and matching camera pin configuration.
2. Open the ESP32 `CameraWebServer` example, enter the test Wi-Fi SSID/password,
   enable the correct camera model, then flash it. Many boards require GPIO 0
   grounded only while flashing and a stable 5 V supply; follow the board vendor's
   wiring because pinouts and upload procedure vary.
3. Open the serial monitor at the baud rate used by the sketch and note its LAN IP.
   From the laptop, open the example's web page and confirm that a still capture is
   a valid JPEG. The exact route varies by sketch/firmware (commonly `/capture`),
   so verify it instead of assuming it.
4. Set `ESP32_CAPTURE_URL` to that verified still-JPEG URL and run the CLI/API
   commands in the root README. Keep the laptop and camera on a mutually reachable
   trusted network.
5. Manually test focus, exposure, distance, angle, and illumination. Collect photos
   of passenger cars, sports cars, supercars, empty scenes, and multiple vehicles.
   Include readable, dark, blurry, and angled plates. Record consented ground truth
   in `data/labels.csv`.

Hardware-dependent checks that software tests cannot perform: power stability,
Wi-Fi reliability/latency, correct endpoint/content type, camera orientation and
   focus, plate pixel size, Gemini classification behavior for the requested vehicle
classes, and Thai OCR accuracy on this camera's JPEG compression.

## Test from this Mac

With the included firmware, plug the camera's MB adapter into the Mac and run
this in the VS Code terminal from the project directory:

```bash
.venv/bin/python -m app.camera_setup
```

This finds the USB adapter, resets the ESP32, and reads its Wi-Fi address. If it
can fetch and save a photo to `data/samples/camera_test.jpg`, it updates
`ESP32_CAPTURE_URL` in `.env`. Close other serial monitors before running it.
Use `--port` if more than one USB serial adapter is attached. Run
`.venv/bin/python -m pip install -r requirements.txt` if `serial` is missing.

If you already know the camera address, you can test it directly:

```bash
source .venv/bin/activate
python -m app.cli --camera-url http://YOUR_CAMERA_IP/capture --capture-only data/samples/camera_test.jpg
```

Replace `YOUR_CAMERA_IP` with the address printed by your board. The command
prints `status: captured`, the saved path, and image dimensions. It calls neither
Gemini nor PaddleOCR. Open the photo and check that the plate is large, sharp, and
well lit. The output path is overwritten if it already exists.

Set your verified endpoint as `ESP32_CAPTURE_URL` in `.env`, then run:

```bash
python -m app.cli --camera-url
# If automatic plate cropping misses, supply coordinates from the original photo:
python -m app.cli --image data/samples/camera_test.jpg --plate-bbox 336,600,680,712
```

The second command's coordinates are examples; replace them for your image.
Set `GEMINI_API_KEY` for entrance classification and download OCR models with
`python scripts/setup_paddleocr.py`.
Output should include `vehicle_type`, `license_plate`, `ocr_engine: paddleocr-v5-th`, and
the review status. OCR transcribes all detected text in the plate crop, so a
province line included in that crop can appear in `license_plate` too.

For the HTTP API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
# In another terminal (after configuring ESP32_CAPTURE_URL):
curl -X POST http://127.0.0.1:8000/capture
```

## Troubleshooting

- `Brownout detector was triggered`: the ESP32 supply voltage dropped. Use a
  short, good USB data cable and stable power (for example, a properly powered
  USB hub), then power-cycle the board and read its serial log again. Camera
  initialization can succeed before Wi-Fi activity triggers a reset. See
  [Espressif's brownout explanation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/fatal-errors.html#brownout).
- Connection timeout/refused: check the camera's current IP, power, Wi-Fi, and
  whether the router isolates wireless clients. USB powers/flashes the board;
  this application fetches pictures over Wi-Fi.
- Non-image response: use the JPEG `/capture` route, not `/` or the MJPEG stream.
  Espressif's [CameraWebServer source](https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP32/examples/Camera/CameraWebServer/app_httpd.cpp)
  defines the capture endpoint separately from streaming.
- No plate found: inspect the saved photo, improve focus/lighting, or use a manual
  plate bounding box. A vehicle classification can succeed even if OCR needs review.
- Missing PaddleOCR models: run `python scripts/setup_paddleocr.py` from the
  repository directory. Downloaded models are reused locally.

