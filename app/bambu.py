"""Read-only local status collector for a Bambu Lab printer."""

import asyncio
import copy
import json
import os
import ssl
import threading
import uuid

import paho.mqtt.client as mqtt

from app import db


PORT = 8883


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fan_percent(value):
    """Bambu fan settings are commonly reported as a 0–15 gear value."""
    value = number(value)
    if value is None:
        return None
    return value / 15 * 100 if 0 <= value <= 15 else value


def merge(base, update):
    """Merge Bambu's incremental MQTT reports into the current status state."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def snapshot(state):
    report = state.get("print", {})
    ams = report.get("ams", {})
    first_ams = next(iter(ams.get("ams", [])), {}) if isinstance(ams, dict) else {}
    return {
        "source": "bambu_x2d",
        "print_state": report.get("gcode_state"),
        "print_percent": number(report.get("mc_percent", report.get("percent"))),
        "remaining_minutes": number(report.get("mc_remaining_time", report.get("remain_time"))),
        "layer_num": number(report.get("layer_num")),
        "total_layer_num": number(report.get("total_layer_num")),
        "nozzle_temp_c": number(report.get("nozzle_temper")),
        "nozzle_target_c": number(report.get("nozzle_target_temper")),
        "bed_temp_c": number(report.get("bed_temper")),
        "bed_target_c": number(report.get("bed_target_temper")),
        "cooling_fan_pct": fan_percent(report.get("cooling_fan_speed")),
        "chamber_fan_pct": max(value for value in (fan_percent(report.get("big_fan1_speed")), fan_percent(report.get("big_fan2_speed"))) if value is not None) if any(value is not None for value in (fan_percent(report.get("big_fan1_speed")), fan_percent(report.get("big_fan2_speed")))) else None,
        "ams_temperature_c": number(first_ams.get("temp")),
        "ams_humidity_index": number(first_ams.get("humidity")),
        "error_code": str(report.get("err")) if report.get("err") not in (None, "0", 0) else None,
    }


def certificate_context(host):
    certificate_path = db.DATA_DIR / "bambu-printer.pem"
    if not certificate_path.exists():
        certificate_path.write_text(ssl.get_server_certificate((host, PORT)), encoding="ascii")
    context = ssl.create_default_context(cafile=str(certificate_path))
    context.check_hostname = False
    return context


class BambuCollector:
    """Maintains a local MQTT subscription and stores a snapshot each minute."""

    def __init__(self):
        self.host = os.getenv("ENERGY_BAMBU_HOST", "").strip()
        self.serial = os.getenv("ENERGY_BAMBU_SERIAL", "").strip()
        self.access_code = os.getenv("ENERGY_BAMBU_ACCESS_CODE", "").strip()
        self.client = None
        self.state = {}
        self.lock = threading.Lock()
        self.last_error = None

    @property
    def configured(self):
        return all((self.host, self.serial, self.access_code))

    def start(self):
        if self.client is not None:
            return
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"home-energy-bambu-{uuid.uuid4()}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set("bblp", self.access_code)
        client.tls_set_context(certificate_context(self.host))

        def on_connect(mqtt_client, userdata, connect_flags, reason_code, properties):
            if reason_code == 0:
                mqtt_client.subscribe(f"device/{self.serial}/report")
                self.last_error = None
            else:
                self.last_error = f"MQTT connection rejected: {reason_code}"

        def on_connect_fail(mqtt_client, userdata):
            self.last_error = "Could not connect to the printer's local MQTT service."

        def on_message(mqtt_client, userdata, message):
            try:
                payload = json.loads(message.payload.decode("utf-8"))
                with self.lock:
                    merge(self.state, payload)
                self.last_error = None
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.last_error = f"Invalid local status message: {exc}"

        client.on_connect = on_connect
        client.on_connect_fail = on_connect_fail
        client.on_message = on_message
        client.connect_async(self.host, PORT, keepalive=30)
        client.loop_start()
        self.client = client

    async def collect_once(self):
        if not self.configured:
            return
        try:
            await asyncio.to_thread(self.start)
            with self.lock:
                current = copy.deepcopy(self.state)
            if current:
                db.save_bambu_reading(snapshot(current))
            elif self.last_error:
                db.save_bambu_reading(error=self.last_error)
        except Exception as exc:
            self.last_error = str(exc)
            db.save_bambu_reading(error=self.last_error)

    async def shutdown(self):
        if self.client is not None:
            self.client.disconnect()
            self.client.loop_stop()
            self.client = None
