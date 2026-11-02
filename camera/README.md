# ESP32-CAM still capture setup (manual)

The ESP32-CAM captures JPEGs only; the laptop sends images to the configured
Gemini AI service for vehicle analysis and OCR.

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
5. Manually test focus, exposure, distance, angle, and illumination. Collect car,
   sportcar, supercar, empty, and multiple-object images plus readable/dark/blurry/angled
   plates. Record consented ground truth in `data/labels.csv`.

Hardware-dependent checks that software tests cannot perform: power stability,
Wi-Fi reliability/latency, correct endpoint/content type, camera orientation and
focus, plate pixel size, Gemini classification behavior for the requested vehicle
classes, and Thai OCR accuracy on this camera's JPEG compression.

