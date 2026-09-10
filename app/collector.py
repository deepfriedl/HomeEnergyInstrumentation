import asyncio
import json
import urllib.request

from app import db


def pick(payload, *paths):
    for path in paths:
        value = payload
        try:
            for part in path.split("."):
                value = value[int(part)] if isinstance(value, list) else value[part]
            if value is not None:
                return float(value)
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return None


def fetch(device):
    errors = []
    for endpoint in ("rpc/Switch.GetStatus?id=0", "status"):
        try:
            with urllib.request.urlopen(f"http://{device['ip_address']}/{endpoint}", timeout=8) as response:
                payload = json.load(response)
            return payload, None
        except Exception as exc:  # Keep collecting other devices after any failure.
            errors.append(str(exc))
    return None, "; ".join(errors)


async def collect_once():
    for device in db.enabled_devices():
        payload, error = await asyncio.to_thread(fetch, device)
        if error:
            db.save_reading(device["id"], error=error)
            continue
        db.save_reading(device["id"],
            watts=pick(payload, "apower", "power", "switch:0.apower", "switch.0.apower"),
            voltage=pick(payload, "voltage", "switch:0.voltage", "switch.0.voltage"),
            current=pick(payload, "current", "switch:0.current", "switch.0.current"),
            total_kwh=pick(payload, "aenergy.total", "meters.0.total") / 1000 if pick(payload, "aenergy.total", "meters.0.total") is not None else None,
            payload=payload)
