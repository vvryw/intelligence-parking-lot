from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path
from time import monotonic, sleep

from dotenv import dotenv_values, set_key
from serial import Serial, SerialException
from serial.tools.list_ports import comports

from .image_source import ImageSourceError, fetch_camera_image, save_image
from .cameras import CAMERA_LABELS

PROJECT_DIR = Path(__file__).resolve().parents[1]
CAPTURE_ADDRESS = re.compile(rb"ESP32_CAPTURE_URL=http://([0-9.]+)/capture\s")


def find_camera_port() -> str:
    ports = sorted(
        port.device
        for port in comports()
        if port.vid is not None or "usbserial" in port.device or "usbmodem" in port.device
    )
    if not ports:
        raise ValueError("No USB serial adapter found. Connect the ESP32-CAM-MB with a USB data cable.")
    if len(ports) > 1:
        raise ValueError(f"Multiple USB serial adapters found ({', '.join(ports)}). Select the camera with --port.")
    return ports[0]


def discover_camera_url(device: str, timeout: float) -> str:
    serial_log = bytearray()
    with Serial(port=None, baudrate=115200, timeout=0.25) as port:
        port.port = device
        port.dtr = False
        port.rts = False
        port.open()
        port.reset_input_buffer()
        port.rts = True
        sleep(0.15)
        port.rts = False

        deadline = monotonic() + timeout
        while monotonic() < deadline:
            chunk = port.read(port.in_waiting or 1)
            if not chunk:
                continue
            serial_log.extend(chunk)
            match = CAPTURE_ADDRESS.search(serial_log)
            if match:
                address = ipaddress.IPv4Address(match[1].decode("ascii"))
                return f"http://{address}/capture"

    brownouts = serial_log.count(b"Brownout detector was triggered")
    if brownouts:
        details = f"The ESP32 reported {brownouts} brownout reset(s) before it could join Wi-Fi."
        if b"Camera init failed: 0x105" in serial_log:
            details += " It also reported camera sensor detection error 0x105."
        raise ValueError(f"{details} Try a short USB data cable and a powered USB hub.")
    if b"Camera init failed" in serial_log:
        raise ValueError("The ESP32 could not initialize the camera. Check its ribbon cable and power.")
    if b"Connecting to Wi-Fi" in serial_log:
        reasons = re.findall(rb"Wi-Fi disconnect reason: (\d+)", serial_log)
        if reasons and reasons[-1] == b"201":
            raise ValueError("The ESP32 could not find the configured Wi-Fi network (201). Check its name, 2.4 GHz availability, and signal.")
        if reasons and reasons[-1] in {b"202", b"204"}:
            code = reasons[-1].decode("ascii")
            raise ValueError(f"Wi-Fi authentication or handshake failed ({code}). Check the password and router security settings.")
        detail = f" Last disconnect reason: {reasons[-1].decode('ascii')}." if reasons else ""
        raise ValueError(f"The camera did not get an IP address.{detail} Check its 2.4 GHz Wi-Fi settings and signal.")
    raise ValueError("No camera URL reported. Check the selected port and firmware, or press RST during the next run.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and test the ESP32-CAM over USB and Wi-Fi")
    parser.add_argument("--port", help="USB serial port (auto-detected by default)")
    parser.add_argument("--camera", choices=CAMERA_LABELS, default="entrance", help="Camera role to configure")
    parser.add_argument("--timeout", type=int, default=30, help="Seconds to wait for Wi-Fi (default: 30)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        device = args.port or find_camera_port()
        print(f"Waiting for the ESP32-CAM on {device}…", flush=True)
        url = discover_camera_url(device, args.timeout)
        print(f"Camera reported {url}", flush=True)
        try:
            image, _ = fetch_camera_image(url)
        except ImageSourceError as exc:
            raise ValueError(f"{exc}. Check that the Mac and camera can reach each other over Wi-Fi.") from exc
        config = PROJECT_DIR / ".env"
        other = "exit" if args.camera == "entrance" else "entrance"
        if dotenv_values(config).get(f"ESP32_{other.upper()}_CAPTURE_URL") == url:
            raise ValueError(f"This address is already assigned to {other}; choose the other board's USB port.")
        destination = save_image(image, args.output or PROJECT_DIR / f"data/samples/{args.camera}_camera_test.jpg")
        set_key(config, f"ESP32_{args.camera.upper()}_CAPTURE_URL", url)
        if args.camera == "entrance":
            set_key(config, "ESP32_CAPTURE_URL", url)
    except SerialException as exc:
        print(f"Setup failed: {exc}. Close any serial monitor using the port and retry.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1

    height, width = image.shape[:2]
    print(f"Saved {width} × {height} photo to {destination}")
    print(f"Configured {CAMERA_LABELS[args.camera]} in the project's .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
