"""Read-only, bounded analytics used by the Home Energy assistant.

These functions deliberately return compact, explainable facts instead of raw
telemetry or arbitrary SQL results.  They are also the future MCP tool surface.
"""

from datetime import timedelta

from app import db


PERIODS = tuple(db.RANGES)


def _period(period):
    period = period if period in db.RANGES else "7d"
    hours, resolution = db.RANGES[period]
    return period, hours, resolution, db.iso(db.now() - timedelta(hours=hours))


def _resolved_device(name):
    if not name:
        return None
    return next((device for device in db.devices() if device["name"].casefold() == str(name).casefold()), None)


def current_conditions():
    """Return the latest normalized state without LAN addresses or raw payloads."""
    devices, _ = db.overview("24h")
    hvac = db.hvac_latest() or {}
    weather = db.weather_latest() or {}
    printer = db.bambu_latest() or {}
    return {
        "source": "latest collected readings",
        "devices": [
            {
                "name": device["name"],
                "enabled": bool(device["enabled"]),
                "watts": device.get("watts"),
                "voltage": device.get("voltage"),
                "observed_at": device.get("observed_at"),
                "has_error": bool(device.get("error")),
            }
            for device in devices
        ],
        "hvac": {key: hvac.get(key) for key in (
            "observed_at", "indoor_temp_f", "indoor_humidity_pct", "outdoor_temp_f",
            "system_mode", "operation", "blower_cfm", "alert_count",
        )},
        "weather": {key: weather.get(key) for key in (
            "observed_at", "temperature_f", "humidity_pct", "dewpoint_f", "wind_mph",
            "wind_direction", "conditions",
        )},
        "printer": {key: printer.get(key) for key in (
            "observed_at", "print_state", "print_percent", "remaining_minutes", "layer_num",
            "total_layer_num", "nozzle_temp_c", "nozzle_target_c", "bed_temp_c",
            "bed_target_c",
        )},
        "has_hvac_error": bool(hvac.get("error")),
        "has_weather_error": bool(weather.get("error")),
        "has_printer_error": bool(printer.get("error")),
    }


def energy_summary(period="7d", device=None):
    """Summarize measured device energy and coverage for an approved period."""
    period, hours, resolution, cutoff = _period(period)
    chosen = _resolved_device(device)
    if device and not chosen:
        return {"error": f"No configured device named {device!r}.", "available_devices": [d["name"] for d in db.devices()]}

    filters, params = ["r.observed_at >= ?"], [cutoff]
    if chosen:
        filters.append("d.id = ?")
        params.append(chosen["id"])
    where = " AND ".join(filters)

    with db.connection() as conn:
        if resolution == "raw":
            rows = conn.execute(
                f"""SELECT d.name, COUNT(*) AS attempts, COUNT(r.watts) AS measurements,
                  ROUND(AVG(r.watts), 1) AS avg_watts, ROUND(MAX(r.watts), 1) AS peak_watts,
                  ROUND(MIN(r.voltage), 1) AS low_voltage, ROUND(MAX(r.voltage), 1) AS high_voltage,
                  ROUND(MAX(r.total_kwh) - MIN(r.total_kwh), 3) AS energy_kwh,
                  'metered counter change' AS energy_method,
                  SUM(CASE WHEN r.error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                  MIN(r.observed_at) AS first_observation, MAX(r.observed_at) AS last_observation
                FROM readings r JOIN devices d ON d.id = r.device_id
                WHERE {where}
                GROUP BY d.id ORDER BY energy_kwh DESC, d.name""",
                params,
            ).fetchall()
        else:
            rollup_filters, rollup_params = ["r.resolution = ?", "r.bucket_start >= ?"], [resolution, cutoff]
            if chosen:
                rollup_filters.append("d.id = ?")
                rollup_params.append(chosen["id"])
            rollup_where = " AND ".join(rollup_filters)
            rows = conn.execute(
                f"""SELECT d.name, SUM(r.sample_count) AS attempts, SUM(r.sample_count) AS measurements,
                  ROUND(SUM(r.avg_watts * r.sample_count) / SUM(r.sample_count), 1) AS avg_watts,
                  ROUND(MAX(r.max_watts), 1) AS peak_watts,
                  ROUND(MIN(r.min_voltage), 1) AS low_voltage, ROUND(MAX(r.max_voltage), 1) AS high_voltage,
                  ROUND(SUM(r.avg_watts * r.sample_count) / 60000.0, 3) AS energy_kwh,
                  'estimated from rollups' AS energy_method,
                  NULL AS errors, MIN(r.bucket_start) AS first_observation, MAX(r.bucket_start) AS last_observation
                FROM rollups r JOIN devices d ON d.id = r.device_id
                WHERE {rollup_where}
                GROUP BY d.id ORDER BY energy_kwh DESC, d.name""",
                rollup_params,
            ).fetchall()
    return {
        "period": period,
        "hours": hours,
        "resolution": resolution,
        "note": "Energy can be a direct meter-counter change or an estimate from retained rollups. Partial coverage can understate use.",
        "devices": [dict(row) for row in rows],
    }


def supply_voltage(period="7d"):
    """Summarize the estimated supply series and explicitly count inferred outages."""
    period, hours, resolution, _ = _period(period)
    points = db.supply_series(period)
    measured = [point["voltage"] for point in points if point.get("voltage") is not None]
    outages = sum(1 for point in points if point.get("inferred_outage"))
    return {
        "period": period,
        "hours": hours,
        "resolution": resolution,
        "measured_points": len(measured),
        "lowest_voltage": round(min(measured), 1) if measured else None,
        "average_voltage": round(sum(measured) / len(measured), 1) if measured else None,
        "highest_voltage": round(max(measured), 1) if measured else None,
        "inferred_outage_points": outages,
        "note": "An inferred outage requires every enabled plug to explicitly fail in the same interval. Missing telemetry is not treated as zero volts.",
    }


def collection_health(period="7d"):
    """Report collector errors by device without exposing network configuration."""
    period, hours, _, cutoff = _period(period)
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT d.name, COUNT(*) AS attempts, COUNT(r.watts) AS successful_measurements,
              SUM(CASE WHEN r.error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
              MAX(r.observed_at) AS last_attempt,
              MAX(CASE WHEN r.watts IS NOT NULL THEN r.observed_at END) AS last_success
            FROM devices d LEFT JOIN readings r ON r.device_id=d.id AND r.observed_at>=?
            GROUP BY d.id ORDER BY errors DESC, d.name""",
            (cutoff,),
        ).fetchall()
    return {"period": period, "hours": hours, "devices": [dict(row) for row in rows]}


def printer_activity(period="24h"):
    """Compare the printer's normalized state against its linked Shelly plug."""
    period = period if period in ("24h", "7d") else "24h"
    _, hours, _, cutoff = _period(period)
    power_device = db.bambu_power_device()
    with db.connection() as conn:
        printer = conn.execute(
            """SELECT COUNT(*) AS samples,
              SUM(CASE WHEN print_state='RUNNING' THEN 1 ELSE 0 END) AS running_samples,
              MIN(observed_at) AS first_observation, MAX(observed_at) AS last_observation,
              MAX(print_percent) AS highest_progress_pct,
              SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
            FROM bambu_readings WHERE observed_at>=?""",
            (cutoff,),
        ).fetchone()
        power = None
        if power_device:
            power = conn.execute(
                """SELECT COUNT(watts) AS measurements, ROUND(AVG(watts), 1) AS avg_watts,
                  ROUND(MAX(watts), 1) AS peak_watts, ROUND(MAX(total_kwh)-MIN(total_kwh), 3) AS metered_kwh,
                  SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
                FROM readings WHERE device_id=? AND observed_at>=?""",
                (power_device["id"], cutoff),
            ).fetchone()
    return {
        "period": period,
        "hours": hours,
        "printer": dict(printer),
        "power_device": power_device["name"] if power_device else None,
        "power": dict(power) if power else None,
        "note": "Printer state and Shelly power are independently collected once per minute; matching timing indicates association, not proof of causation.",
    }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "current_conditions",
            "description": "Get the most recent device, HVAC, weather, and printer readings.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "energy_summary",
            "description": "Summarize energy, average and peak power, voltage range, and coverage for one device or all devices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": list(PERIODS)},
                    "device": {"type": "string", "description": "Exact configured device name, if a single device is requested."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supply_voltage",
            "description": "Summarize estimated supply voltage, including explicitly inferred outages.",
            "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": list(PERIODS)}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collection_health",
            "description": "Check measurement coverage and polling errors by configured device.",
            "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": list(PERIODS)}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "printer_activity",
            "description": "Compare Bambu printer state with its linked Shelly power data for the last 24 hours or 7 days.",
            "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": ["24h", "7d"]}}},
        },
    },
]


_TOOLS = {
    "current_conditions": current_conditions,
    "energy_summary": energy_summary,
    "supply_voltage": supply_voltage,
    "collection_health": collection_health,
    "printer_activity": printer_activity,
}


def run_tool(name, arguments):
    """Call one allow-listed analytics function; unknown inputs cannot reach SQL."""
    tool = _TOOLS.get(name)
    if not tool:
        return {"error": f"Unknown analytics tool: {name}"}
    try:
        return tool(**(arguments if isinstance(arguments, dict) else {}))
    except TypeError:
        return {"error": "Invalid parameters for the requested analytics tool."}
