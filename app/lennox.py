"""Read-only, local telemetry collector for a Lennox S40 thermostat."""

import asyncio
import os
import time

from lennoxs30api import s30api_async

from app import db


APP_ID = "home_energy_instrumentation"


def number(value):
    """Convert an available Lennox diagnostic to a number; ignore placeholders."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diagnostics_by_name(system):
    values = {}
    for equipment in system.equipment.values():
        for diagnostic in equipment.diagnostics.values():
            if diagnostic.name and diagnostic.value not in (None, "", "waiting..."):
                values[diagnostic.name] = diagnostic.value
    return values


def active_zone(system):
    return next((zone for zone in system.zone_list if zone.is_zone_active()), None)


def snapshot(system):
    """Make a safe, structured state record from the public S40 model objects."""
    zone = active_zone(system)
    diagnostics = diagnostics_by_name(system)
    return {
        "source": "lennox_s40",
        "system_name": system.name,
        "product_type": system.productType,
        "indoor_temp_f": number(getattr(zone, "temperature", None)),
        "indoor_humidity_pct": number(getattr(zone, "humidity", None)),
        "outdoor_temp_f": number(system.outdoorTemperature),
        "cooling_rate_pct": number(diagnostics.get("Cooling Rate")),
        "heating_rate_pct": number(diagnostics.get("Heating Rate")),
        "blower_cfm": number(diagnostics.get("Blower CFM Demand")),
        "system_mode": getattr(zone, "systemMode", None),
        "operation": getattr(zone, "tempOperation", None),
        "fan_running": getattr(zone, "fan", None),
        "aux_active": getattr(zone, "aux", None),
        "defrost_active": getattr(zone, "defrost", None),
        "alert_count": system.alerts_num_active or 0,
        "equipment": [
            {
                "name": equipment.equipment_name,
                "type": equipment.equipment_type_name,
                "model": equipment.unit_model_number,
                "diagnostics": diagnostics_by_name_for_equipment(equipment),
            }
            for equipment in system.equipment.values()
        ],
    }


def diagnostics_by_name_for_equipment(equipment):
    return {
        diagnostic.name: {"value": diagnostic.value, "unit": diagnostic.unit, "valid": diagnostic.valid}
        for diagnostic in equipment.diagnostics.values()
        if diagnostic.name and diagnostic.value not in (None, "", "waiting...")
    }


class LennoxCollector:
    """Maintains one local subscription and stores a state snapshot every minute."""

    def __init__(self):
        self.host = os.getenv("ENERGY_LENNOX_HOST")
        self.api = None
        self.system = None

    async def connect(self):
        if not self.host:
            return False
        self.api = s30api_async(
            "", "", APP_ID, ip_address=self.host, pii_message_logs=False,
            message_debug_logging=False, timeout=15, long_poll_delay=2,
        )
        await self.api.serverConnect()
        self.system = self.api.getSystem("LCC")
        await self.api.subscribe(self.system)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            await self.api.messagePump()
            if self.system.config_complete() and self.system.zone_list:
                return True
        return self.system.config_complete()

    async def collect_once(self):
        if not self.host:
            return
        try:
            if self.api is None or self.system is None:
                if not await self.connect():
                    raise RuntimeError("Timed out waiting for Lennox S40 configuration")
            else:
                await self.api.messagePump()
            db.save_hvac_reading(snapshot(self.system))
        except Exception as exc:
            db.save_hvac_reading(error=str(exc))
            await self.shutdown()

    async def shutdown(self):
        if self.api is not None:
            try:
                await self.api.shutdown()
            finally:
                self.api = None
                self.system = None
