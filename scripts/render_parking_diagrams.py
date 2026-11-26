"""Render the full system architecture as SVG and PNG."""
from html import escape
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parts = ['''<svg xmlns="http://www.w3.org/2000/svg" width="2200" height="1650" viewBox="0 0 2200 1650">
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#34546e"/></marker>
  <marker id="pending" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#95531b"/></marker>
</defs>
<rect width="2200" height="1650" fill="#f1f5f9"/>
<style>
  text{font-family:Helvetica,Arial,sans-serif;fill:#142d43}
  .title{font-size:44px;font-weight:700}.subtitle{font-size:22px;fill:#466278}
  .section{font-size:21px;font-weight:700;letter-spacing:1.2px}
  .head{font-size:22px;font-weight:700}.body{font-size:19px}
  .small{font-size:17px;fill:#39566d}
  .label,.pending-label{font-size:17px;paint-order:stroke;stroke:#f1f5f9;stroke-width:8px;stroke-linejoin:round}
  .label{fill:#34546e}.pending-label{fill:#804511}
</style>''']


def text(x, y, lines, cls="body", anchor="start", line_height=27):
    parts.append(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">')
    for i, line in enumerate(lines):
        offset = line_height if i else 0
        parts.append(f'<tspan x="{x}" dy="{offset}">{escape(line)}</tspan>')
    parts.append("</text>")


def panel(x, y, w, h, fill, stroke="#bacbdc", dashed=False):
    dash = ' stroke-dasharray="9 6"' if dashed else ""
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>')


def box(x, y, w, h, title, lines, fill="#ffffff", dashed=False, cls="body"):
    panel(x, y, w, h, fill, "#bc8f61" if dashed else "#bacbdc", dashed)
    text(x + 21, y + 34, [title], "head")
    text(x + 21, y + 66, lines, cls, line_height=25)


def arrow(points, both=False, dashed=False):
    coordinates = " ".join(f"{x},{y}" for x, y in points)
    marker = "pending" if dashed else "arrow"
    stroke = "#95531b" if dashed else "#34546e"
    start = f' marker-start="url(#{marker})"' if both else ""
    dash = ' stroke-dasharray="9 7"' if dashed else ""
    parts.append(f'<polyline points="{coordinates}" fill="none" stroke="{stroke}" stroke-width="2.5" stroke-linejoin="round" marker-end="url(#{marker})"{start}{dash}/>')


def label(x, y, *lines, dashed=False):
    text(x, y, lines, "pending-label" if dashed else "label", "middle", 22)


text(45, 64, ["Intelligence Parking Lot · One-laptop setup"], "title")
text(46, 102, ["Recommended demo: one laptop handles both camera feeds, sensor telemetry and dashboards"], "subtitle")
arrow([(1630, 102), (1685, 102)])
text(1702, 108, ["Implemented"], "small")
arrow([(1851, 102), (1906, 102)], dashed=True)
text(1923, 108, ["Prepared / future"], "small")

panel(35, 143, 2130, 247, "#e8eff8")
text(60, 182, ["INTERNET SERVICES"], "section")
box(60, 203, 415, 155, "Google Gemini", [
    "Receives the entrance JPEG.",
    "Returns vehicle type + plate box.",
    "Classes: car, sportcar, supercar.",
], "#f4efff")
box(515, 203, 465, 155, "Supabase · PostgreSQL", [
    "2 VIP spaces + 3 normal spaces",
    "Visits, decisions, occupancy and timestamps",
    "Assign or release a bay in one transaction",
], "#e6f5ee")
box(1020, 203, 470, 155, "Supabase · Private photo bucket", [
    "Receives original JPEG + enhanced crop.",
    "Returns stored JPEGs to the parking server.",
    "PostgreSQL stores the linked media IDs.",
], "#e6f5ee")
box(1610, 203, 530, 155, "InfluxDB Cloud · Sensor archive", [
    "Optional sensor cloud sync from the laptop",
    "Archives raw UNO and ESP32 telemetry.",
    "Returns history on demand to Flask.",
], "#e6f5ee")

panel(20, 462, 2160, 638, "#f9fbfd", "#839db7")
text(47, 493, ["WINDOWS LAPTOP  ·  LOCAL MIDDLE SERVER"], "section")
text(1550, 493, ["Run each app in its own Python environment"], "small", "end")

panel(35, 508, 1095, 290, "#e7f0ff")
text(58, 545, ["PARKING AND CAMERA SERVICE  ·  FASTAPI  :8000"], "section")
box(60, 563, 295, 165, "1. Camera capture", [
    "Requests a still from either role.",
    "Corrects image orientation.",
    "Streams previews to the browser.",
], cls="small")
box(390, 563, 337, 165, "2. Recognition + archive", [
    "Entry: Gemini classification + box",
    "Both: crop, enhance, PaddleOCR",
    "Save JPEG, crop and observation ID.",
], cls="small")
box(762, 563, 343, 165, "3. Parking decision API", [
    "Entry: check matching bay capacity.",
    "Exit: match plate and release its bay.",
    "Return decision, counts and history.",
], cls="small")
arrow([(355, 644), (389, 644)])
arrow([(727, 644), (761, 644)])
text(60, 770, ["Local development can use SQLite and disk photos. The shared deployment uses Supabase."], "small")

panel(1210, 508, 955, 290, "#fff1df")
text(1233, 545, ["SENSOR DASHBOARD SERVICES"], "section")
box(1235, 563, 260, 165, "Mosquitto :1883", [
    "Listens on the laptop's LAN IP.",
    "Authenticates gateway clients.",
    "Delivers messages to Flask.",
], cls="small")
box(1530, 563, 280, 165, "Flask :5000", [
    "Validates messages + freshness.",
    "Serves /api/latest and history.",
    "Hands telemetry to storage.",
], cls="small")
box(1845, 563, 295, 165, "InfluxDB OSS :8086", [
    "Local time-series database.",
    "Receives validated telemetry.",
    "Returns history to Flask.",
], cls="small")
arrow([(1495, 644), (1529, 644)])
arrow([(1810, 644), (1844, 644)], both=True)
text(1235, 770, ["When enabled, the cloud uploader queues raw telemetry and uses HTTPS with server-side credentials."], "small")

arrow([(615, 508), (615, 442), (265, 442), (265, 359)], both=True)
label(395, 431, "HTTPS · JPEG ⇄ class + plate box")
arrow([(882, 508), (882, 441), (745, 441), (745, 359)], both=True)
label(810, 424, "HTTPS · records + results")
arrow([(1035, 508), (1035, 442), (1215, 442), (1215, 359)], both=True)
label(1135, 425, "HTTPS · upload / fetch JPEGs")
arrow([(1875, 508), (1875, 359)], both=True)
label(1875, 434, "HTTPS · optional telemetry writes / history queries")

box(135, 920, 440, 155, "Parking dashboard · Browser", [
    "Open 127.0.0.1:8000 on this laptop.",
    "Shows live images, plate, visit and photos.",
    "Runs the four-second gate simulator.",
], "#e7f0ff")
box(800, 909, 650, 180, "Prepared · Sensor dashboard bridge", [
    "Reads GET /api/latest on 127.0.0.1:5000 with Basic Auth.",
    "Maps A1 to a verified bay ID and skips stale readings.",
    "POST /parking/sensors on 127.0.0.1:8000 with the integration key.",
    "Dashboard backend can request parking counts, visits and photos.",
], "#fff1df", dashed=True)
box(1640, 920, 485, 155, "Sensor dashboard · Browser", [
    "Open 127.0.0.1:5000 on this laptop.",
    "Shows A1 occupancy and environmental charts.",
    "Can select local or configured cloud history.",
], "#fff1df")
arrow([(350, 920), (350, 798)], both=True)
label(350, 861, "Loopback HTTP · port 8000", "Preview / capture ⇄ decision")
arrow([(1875, 920), (1875, 837), (1730, 837), (1730, 799)], both=True)
label(1875, 855, "Loopback HTTP · live state and history")
arrow([(1180, 909), (1180, 823), (1620, 823), (1620, 799)], both=True, dashed=True)
label(1374, 848, "GET /api/latest ⇄ sensor snapshot", dashed=True)
arrow([(850, 909), (850, 799)], dashed=True)
label(850, 867, "POST fresh mapped bay readings", dashed=True)

panel(35, 1155, 2130, 453, "#e9eff5")
text(155, 1195, ["FIELD HARDWARE"], "section")
text(1170, 1195, ["The laptop and ESP32 boards share reachable Wi-Fi."], "small")
box(62, 1258, 345, 145, "Entrance ESP32-CAM", [
    "OV2640 supplies a JPEG still.",
    "HTTP /capture over local Wi-Fi",
    "5 V USB via ESP32-CAM-MB",
], "#e7f0ff")
box(436, 1258, 345, 145, "Exit ESP32-CAM", [
    "OV2640 supplies a JPEG still.",
    "HTTP /capture over local Wi-Fi",
    "5 V USB via ESP32-CAM-MB",
], "#e7f0ff")
box(808, 1258, 335, 145, "Future gate controller", [
    "Arrival / departure trigger",
    "Gate command and acknowledgement",
    "Physical servo awaits integration.",
], "#fff1df", dashed=True, cls="small")
box(1170, 1258, 273, 145, "A1 ultrasonic", [
    "HC-SR04 distance to obstacle",
    "UNO D2 TRIG / D3 ECHO",
    "Powered by UNO 5 V + GND",
], "#fff1df", cls="small")
box(1470, 1258, 255, 145, "Arduino UNO", [
    "Measures echo pulse.",
    "Computes A1 occupancy.",
    "Sends UART telemetry.",
], "#fff1df", cls="small")
box(1760, 1258, 377, 145, "ESP32 sensor gateway", [
    "UART RX GPIO25 · 9600 baud",
    "Publishes UNO and environment JSON.",
    "Wi-Fi uplink to Mosquitto :1883",
], "#fff1df", cls="small")
box(1760, 1460, 377, 115, "Environmental sensors", [
    "DHT22 GPIO4 · LDR GPIO34 · MQ-2 GPIO35",
    "Temperature/humidity, light and gas raw ADC",
], "#fff1df", cls="small")
arrow([(97, 798), (97, 1257)], both=True)
arrow([(125, 798), (125, 1109), (607, 1109), (607, 1257)], both=True)
label(345, 1128, "Laptop sends GET /capture · Cameras return JPEG")
arrow([(1443, 1330), (1469, 1330)], both=True)
arrow([(1725, 1330), (1759, 1330)])
text(1470, 1441, ["UNO D11 TX → level divider → GPIO25 RX · common GND"], "small")
arrow([(1960, 1460), (1960, 1404)])
arrow([(2110, 1258), (2110, 1122), (2153, 1122), (2153, 744), (1350, 744), (1350, 728)])
label(1895, 1117, "MQTT · parking/node/telemetry + parking/gateway/telemetry")
arrow([(720, 798), (720, 1135), (975, 1135), (975, 1257)], both=True, dashed=True)
label(956, 1130, "Future trigger ⇄ gate command / ack", dashed=True)
text(60, 1475, ["Current physical bay coverage"], "head")
text(60, 1508, [
    "A1 is installed. M1 and vibration are pending.",
    "Five monitored bays require more channels and verified bay mapping.",
    "The LCD and servo are reserved for later hardware work.",
], "body", line_height=27)

parts.append("</svg>")
svg = ROOT / "docs/parking-system-flow.svg"
svg.write_text("\n".join(parts) + "\n", encoding="utf-8")
subprocess.run(["rsvg-convert", str(svg), "-o", str(svg.with_suffix(".png"))], check=True)
