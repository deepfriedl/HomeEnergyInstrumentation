"""Read-only inventory of a local Lennox S40 system.

This is deliberately separate from the scheduled collector.  It lets us learn
which fields this specific installation provides before deciding what to retain
as regular telemetry.
"""

import asyncio
import json
import os
import time
from pathlib import Path

from lennoxs30api import s30api_async


APP_ID = "home_energy_instrumentation"
OUTPUT_PATH = Path(os.getenv("ENERGY_LENNOX_DISCOVERY_OUTPUT", "/app/data/lennox-discovery.json"))
SYSTEM_FIELDS = (
    "name", "productType", "indoorUnitType", "outdoorUnitType",
    "outdoorTemperature", "outdoorTemperatureC", "outdoorTemperatureStatus",
    "temperatureUnit", "numberOfZones", "single_setpoint_mode",
    "manualAwayMode", "ventilationMode", "dehumidificationMode",
    "humidificationMode", "allergenDefender", "alert",
    "alerts_num_active", "alerts_num_cleared", "softwareVersion",
)
ZONE_FIELDS = (
    "id", "name", "temperature", "temperatureC", "temperatureStatus",
    "humidity", "humidityStatus", "systemMode", "tempOperation", "fan",
    "fanMode", "heatCoast", "coolCoast", "defrost", "aux", "ssr",
    "demand", "damper", "ventilation", "humOperation", "humidityMode",
    "csp", "cspC", "hsp", "hspC", "sp", "spC", "desp", "husp",
)


def populated_fields(source, field_names):
    """Return only public fields with a value, keeping discovery output useful."""
    return {field: getattr(source, field) for field in field_names if getattr(source, field, None) is not None}


def inventory(system):
    equipment = []
    for equipment_id, item in system.equipment.items():
        diagnostics = [
            {
                "name": diagnostic.name,
                "unit": diagnostic.unit,
                "value": diagnostic.value,
                "valid": diagnostic.valid,
            }
            for diagnostic in item.diagnostics.values()
            if diagnostic.name is not None
        ]
        equipment.append({
            "id": equipment_id,
            "name": item.equipment_name,
            "type": item.equipment_type_name,
            "model": item.unit_model_number,
            "diagnostics": diagnostics,
        })
    return {
        "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "system": populated_fields(system, SYSTEM_FIELDS),
        "zones": [populated_fields(zone, ZONE_FIELDS) for zone in system.zone_list],
        "equipment": equipment,
        "active_alerts": [alert for alert in system.active_alerts if isinstance(alert, (str, int, float, bool))],
    }


async def discover():
    host = os.getenv("ENERGY_LENNOX_HOST")
    if not host:
        raise SystemExit("Set ENERGY_LENNOX_HOST in .env before running discovery.")

    api = s30api_async(
        "", "", APP_ID, ip_address=host, pii_message_logs=False,
        message_debug_logging=False, timeout=15, long_poll_delay=2,
    )
    try:
        await api.serverConnect()
        system = api.getSystem("LCC")
        await api.subscribe(system)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            await api.messagePump()
            if system.config_complete() and system.zone_list:
                break

        result = inventory(system)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Saved Lennox discovery to {OUTPUT_PATH}")
    finally:
        await api.shutdown()


if __name__ == "__main__":
    asyncio.run(discover())
