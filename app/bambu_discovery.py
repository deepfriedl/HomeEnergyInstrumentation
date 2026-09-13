"""One-time, read-only discovery for Bambu Lab local MQTT telemetry."""

import json
import os
import ssl
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt

from app import db


PORT = 8883
WAIT_SECONDS = 30
SENSITIVE_TERMS = ("access", "code", "serial", "device_id", "token", "password", "ssid", "ip", "url", "file", "subtask", "project")


def configured():
    return {
        "host": os.getenv("ENERGY_BAMBU_HOST", "").strip(),
        "serial": os.getenv("ENERGY_BAMBU_SERIAL", "").strip(),
        "access_code": os.getenv("ENERGY_BAMBU_ACCESS_CODE", "").strip(),
    }


def redact(value, key=""):
    """Keep telemetry structure while omitting identifiers, job names, and network details."""
    if any(term in key.lower() for term in SENSITIVE_TERMS):
        return "[redacted]"
    if isinstance(value, dict):
        return {name: redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value


def section_fields(payload):
    return {
        name: sorted(value.keys()) if isinstance(value, dict) else type(value).__name__
        for name, value in payload.items()
    }


def certificate_context(host):
    """Trust the printer's locally retrieved certificate for this private deployment."""
    certificate_path = db.DATA_DIR / "bambu-printer.pem"
    if not certificate_path.exists():
        certificate = ssl.get_server_certificate((host, PORT))
        certificate_path.write_text(certificate, encoding="ascii")
    context = ssl.create_default_context(cafile=str(certificate_path))
    # Bambu's certificate name is its serial number rather than the LAN IP address.
    context.check_hostname = False
    return context


def discover():
    settings = configured()
    if not all(settings.values()):
        raise RuntimeError("Set ENERGY_BAMBU_HOST, ENERGY_BAMBU_SERIAL, and ENERGY_BAMBU_ACCESS_CODE in .env before discovery.")

    report = []
    connected = threading.Event()
    received = threading.Event()
    failure = []
    topic = f"device/{settings['serial']}/report"

    def on_connect(client, userdata, connect_flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(topic)
            connected.set()
        else:
            failure.append(f"MQTT connection rejected: {reason_code}")
            connected.set()

    def on_connect_fail(client, userdata):
        failure.append("Could not connect to the printer's local MQTT service.")
        connected.set()

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            report.append(payload)
            received.set()
        except (UnicodeDecodeError, json.JSONDecodeError):
            failure.append("The printer sent a status message that was not JSON.")
            received.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"home-energy-discovery-{uuid.uuid4()}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", settings["access_code"])
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_message = on_message
    client.tls_set_context(certificate_context(settings["host"]))

    try:
        client.connect(settings["host"], PORT, keepalive=20)
        client.loop_start()
        if not connected.wait(WAIT_SECONDS) or failure:
            raise RuntimeError(failure[0] if failure else "Timed out connecting to the printer.")
        if not received.wait(WAIT_SECONDS):
            raise RuntimeError("Connected, but no local status report arrived within 30 seconds.")
    finally:
        client.disconnect()
        client.loop_stop()

    payload = report[0]
    output = {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "transport": "local MQTT status subscription",
        "report_sections": section_fields(payload),
        "sample": redact(payload),
    }
    output_path = db.DATA_DIR / "bambu-discovery.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return output_path, output["report_sections"]


def main():
    output_path, sections = discover()
    print(f"Saved scrubbed local status sample to {output_path}.")
    print("Report sections:", ", ".join(sections))


if __name__ == "__main__":
    main()
