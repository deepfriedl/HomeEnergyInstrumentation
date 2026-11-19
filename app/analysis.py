"""Read-only, bounded analytics used by the Home Energy assistant.

The model receives compact tool results rather than direct access to the app's
database. ``query_telemetry`` is intentionally more flexible than the summary
tools, but executes only against an in-memory copy of approved telemetry fields.
It is also the future MCP tool surface.
"""

import sqlite3
import time
from datetime import timedelta

from app import db


PERIODS = tuple(db.RANGES)
MAX_SQL_LENGTH = 5_000
MAX_SQL_ROWS = 100
SQL_TIMEOUT_SECONDS = 2

SAFE_SQL_TABLES = frozenset({
    "power_samples", "power_rollups", "supply_samples", "supply_rollups",
    "hvac_samples", "hvac_rollups", "weather_samples", "weather_rollups",
    "printer_samples", "printer_rollups",
})

SAFE_SQL_FUNCTIONS = frozenset({
    "abs", "avg", "coalesce", "count", "date", "datetime", "glob", "group_concat",
    "ifnull", "instr", "julianday", "lag", "lead", "length", "like", "lower",
    "ltrim", "max", "min", "nullif", "printf", "round", "rtrim", "strftime", "substr",
    "sum", "time", "total", "trim", "unixepoch", "upper",
})


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


def _copy_rows(destination, table, source, statement):
    """Copy one explicitly selected, non-sensitive result set into memory."""
    placeholders = ", ".join("?" for _ in destination.execute(f"PRAGMA table_info({table})"))
    destination.executemany(
        f"INSERT INTO {table} VALUES ({placeholders})",
        (tuple(row) for row in source.execute(statement)),
    )


def _telemetry_database():
    """Build an isolated, sanitized SQLite database for one assistant query.

    The original database is never exposed to model-supplied SQL. In particular,
    this copy deliberately excludes users, LAN addresses, raw responses, error
    text, station identifiers, and all configuration.
    """
    safe = sqlite3.connect(":memory:")
    safe.row_factory = sqlite3.Row
    safe.executescript("""
        CREATE TABLE power_samples (
          observed_at TEXT, device_name TEXT, watts REAL, voltage REAL,
          current REAL, total_kwh REAL
        );
        CREATE TABLE power_rollups (
          bucket_start TEXT, device_name TEXT, resolution TEXT, avg_watts REAL,
          max_watts REAL, avg_voltage REAL, min_voltage REAL, max_voltage REAL,
          sample_count INTEGER, energy_kwh REAL
        );
        CREATE TABLE supply_samples (
          observed_at TEXT, voltage REAL, reporting_plugs INTEGER,
          inferred_outage INTEGER
        );
        CREATE TABLE supply_rollups (
          bucket_start TEXT, resolution TEXT, avg_voltage REAL,
          inferred_outage_count INTEGER, sample_count INTEGER
        );
        CREATE TABLE hvac_samples (
          observed_at TEXT, indoor_temp_f REAL, indoor_humidity_pct REAL,
          outdoor_temp_f REAL, cooling_rate_pct REAL, heating_rate_pct REAL,
          blower_cfm REAL, system_mode TEXT, operation TEXT, fan_running INTEGER,
          aux_active INTEGER, defrost_active INTEGER, alert_count INTEGER
        );
        CREATE TABLE hvac_rollups (
          bucket_start TEXT, resolution TEXT, avg_indoor_temp_f REAL,
          avg_indoor_humidity_pct REAL, avg_outdoor_temp_f REAL,
          avg_cooling_rate_pct REAL, max_cooling_rate_pct REAL,
          avg_heating_rate_pct REAL, max_heating_rate_pct REAL,
          avg_blower_cfm REAL, max_blower_cfm REAL, sample_count INTEGER
        );
        CREATE TABLE weather_samples (
          observed_at TEXT, temperature_f REAL, humidity_pct REAL, dewpoint_f REAL,
          wind_mph REAL, wind_direction_degrees REAL,
          precipitation_last_hour_in REAL, conditions TEXT
        );
        CREATE TABLE weather_rollups (
          bucket_start TEXT, resolution TEXT, avg_temperature_f REAL,
          avg_humidity_pct REAL, avg_dewpoint_f REAL, avg_wind_mph REAL,
          total_precipitation_in REAL, sample_count INTEGER
        );
        CREATE TABLE printer_samples (
          observed_at TEXT, print_state TEXT, print_percent REAL,
          remaining_minutes REAL, layer_num REAL, total_layer_num REAL,
          nozzle_temp_c REAL, nozzle_target_c REAL, bed_temp_c REAL,
          bed_target_c REAL, cooling_fan_pct REAL, chamber_fan_pct REAL,
          ams_temperature_c REAL, ams_humidity_index REAL, error_code TEXT
        );
        CREATE TABLE printer_rollups (
          bucket_start TEXT, resolution TEXT, avg_print_percent REAL,
          avg_nozzle_temp_c REAL, avg_nozzle_target_c REAL, avg_bed_temp_c REAL,
          avg_bed_target_c REAL, avg_cooling_fan_pct REAL,
          avg_chamber_fan_pct REAL, avg_ams_temperature_c REAL,
          sample_count INTEGER
        );
        CREATE INDEX power_samples_time ON power_samples(observed_at);
        CREATE INDEX power_rollups_time ON power_rollups(bucket_start, resolution);
        CREATE INDEX hvac_samples_time ON hvac_samples(observed_at);
        CREATE INDEX weather_samples_time ON weather_samples(observed_at);
        CREATE INDEX printer_samples_time ON printer_samples(observed_at);
    """)
    with db.connection() as source:
        _copy_rows(safe, "power_samples", source, """
            SELECT r.observed_at, d.name, r.watts, r.voltage, r.current, r.total_kwh
            FROM readings r JOIN devices d ON d.id=r.device_id
        """)
        _copy_rows(safe, "power_rollups", source, """
            SELECT r.bucket_start, d.name, r.resolution, r.avg_watts, r.max_watts,
              r.avg_voltage, r.min_voltage, r.max_voltage, r.sample_count,
              ROUND(r.avg_watts * r.sample_count / 60000.0, 6)
            FROM rollups r JOIN devices d ON d.id=r.device_id
        """)
        _copy_rows(safe, "supply_samples", source, """
            WITH minute_readings AS (
              SELECT strftime('%Y-%m-%dT%H:%M:00+00:00', r.observed_at) AS observed_at,
                AVG(r.voltage) AS voltage,
                COUNT(DISTINCT CASE WHEN r.voltage IS NOT NULL THEN r.device_id END) AS reporters,
                COUNT(DISTINCT CASE WHEN r.error IS NOT NULL THEN r.device_id END) AS failures
              FROM readings r JOIN devices d ON d.id=r.device_id
              WHERE d.enabled=1
              GROUP BY strftime('%Y-%m-%dT%H:%M:00+00:00', r.observed_at)
            ) SELECT observed_at, CASE WHEN reporters > 0 THEN voltage END, reporters,
              CASE WHEN reporters = 0 AND failures >= (SELECT COUNT(*) FROM devices WHERE enabled=1)
                THEN 1 ELSE 0 END
            FROM minute_readings
        """)
        _copy_rows(safe, "supply_rollups", source, """
            SELECT bucket_start, resolution, avg_voltage, inferred_outage_count, sample_count
            FROM supply_rollups
        """)
        _copy_rows(safe, "hvac_samples", source, """
            SELECT observed_at, indoor_temp_f, indoor_humidity_pct, outdoor_temp_f,
              cooling_rate_pct, heating_rate_pct, blower_cfm, system_mode, operation,
              fan_running, aux_active, defrost_active, alert_count
            FROM hvac_readings
        """)
        _copy_rows(safe, "hvac_rollups", source, """
            SELECT bucket_start, resolution, avg_indoor_temp_f, avg_indoor_humidity_pct,
              avg_outdoor_temp_f, avg_cooling_rate_pct, max_cooling_rate_pct,
              avg_heating_rate_pct, max_heating_rate_pct, avg_blower_cfm,
              max_blower_cfm, sample_count
            FROM hvac_rollups
        """)
        _copy_rows(safe, "weather_samples", source, """
            SELECT observed_at, temperature_f, humidity_pct, dewpoint_f, wind_mph,
              wind_direction_degrees, precipitation_last_hour_in, conditions
            FROM weather_readings
        """)
        _copy_rows(safe, "weather_rollups", source, """
            SELECT bucket_start, resolution, avg_temperature_f, avg_humidity_pct,
              avg_dewpoint_f, avg_wind_mph, total_precipitation_in, sample_count
            FROM weather_rollups
        """)
        _copy_rows(safe, "printer_samples", source, """
            SELECT observed_at, print_state, print_percent, remaining_minutes, layer_num,
              total_layer_num, nozzle_temp_c, nozzle_target_c, bed_temp_c,
              bed_target_c, cooling_fan_pct, chamber_fan_pct, ams_temperature_c,
              ams_humidity_index, error_code
            FROM bambu_readings
        """)
        _copy_rows(safe, "printer_rollups", source, """
            SELECT bucket_start, resolution, avg_print_percent, avg_nozzle_temp_c,
              avg_nozzle_target_c, avg_bed_temp_c, avg_bed_target_c,
              avg_cooling_fan_pct, avg_chamber_fan_pct, avg_ams_temperature_c,
              sample_count
            FROM bambu_rollups
        """)
    safe.commit()
    return safe


def _safe_sql_authorizer(action, argument_one, argument_two, database_name, source):
    """Allow only read operations over the explicitly created safe tables."""
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if argument_one in SAFE_SQL_TABLES else sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if str(argument_two).lower() in SAFE_SQL_FUNCTIONS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def query_telemetry(sql):
    """Run one bounded SELECT query against sanitized, in-memory telemetry tables."""
    sql = str(sql or "").strip()
    if not sql or len(sql) > MAX_SQL_LENGTH:
        return {"error": "Provide one SQL query of up to 5,000 characters."}
    normalized = sql.rstrip(";").strip()
    if ";" in normalized or not normalized.lower().startswith(("select", "with")):
        return {"error": "Only one SELECT query (optionally beginning with WITH) is allowed."}

    safe = _telemetry_database()
    deadline = time.monotonic() + SQL_TIMEOUT_SECONDS
    safe.set_authorizer(_safe_sql_authorizer)
    safe.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1_000)
    try:
        cursor = safe.execute(normalized)
        if cursor.description is None:
            return {"error": "Only result-returning SELECT queries are allowed."}
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(MAX_SQL_ROWS + 1)
        truncated = len(rows) > MAX_SQL_ROWS
        return {
            "columns": columns,
            "rows": [list(row) for row in rows[:MAX_SQL_ROWS]],
            "row_count": min(len(rows), MAX_SQL_ROWS),
            "truncated": truncated,
            "note": "Times are UTC. Raw samples retain seven days; use 5m, hour, or day rollups for older history. power_rollups.energy_kwh is estimated from average watts and sample count.",
        }
    except sqlite3.Error:
        return {"error": "The query could not be run. Use only the documented telemetry tables, columns, and standard SELECT expressions."}
    finally:
        safe.close()


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
    {
        "type": "function",
        "function": {
            "name": "query_telemetry",
            "description": """Run one read-only SELECT query against sanitized telemetry tables. Use this for custom comparisons, weekday/weekend or hourly grouping, trends, and correlations. Never use it to request configuration. Available tables: power_samples(observed_at, device_name, watts, voltage, current, total_kwh; seven days); power_rollups(bucket_start, device_name, resolution, avg_watts, max_watts, avg_voltage, min_voltage, max_voltage, sample_count, energy_kwh; resolutions 5m/hour/day); supply_samples(observed_at, voltage, reporting_plugs, inferred_outage); supply_rollups(bucket_start, resolution, avg_voltage, inferred_outage_count, sample_count); hvac_samples(observed_at, indoor_temp_f, indoor_humidity_pct, outdoor_temp_f, cooling_rate_pct, heating_rate_pct, blower_cfm, system_mode, operation, fan_running, aux_active, defrost_active, alert_count); hvac_rollups(bucket_start, resolution, avg_indoor_temp_f, avg_indoor_humidity_pct, avg_outdoor_temp_f, avg_cooling_rate_pct, max_cooling_rate_pct, avg_heating_rate_pct, max_heating_rate_pct, avg_blower_cfm, max_blower_cfm, sample_count); weather_samples(observed_at, temperature_f, humidity_pct, dewpoint_f, wind_mph, wind_direction_degrees, precipitation_last_hour_in, conditions); weather_rollups(bucket_start, resolution, avg_temperature_f, avg_humidity_pct, avg_dewpoint_f, avg_wind_mph, total_precipitation_in, sample_count); printer_samples(observed_at, print_state, print_percent, remaining_minutes, layer_num, total_layer_num, nozzle_temp_c, nozzle_target_c, bed_temp_c, bed_target_c, cooling_fan_pct, chamber_fan_pct, ams_temperature_c, ams_humidity_index, error_code); printer_rollups(bucket_start, resolution, avg_print_percent, avg_nozzle_temp_c, avg_nozzle_target_c, avg_bed_temp_c, avg_bed_target_c, avg_cooling_fan_pct, avg_chamber_fan_pct, avg_ams_temperature_c, sample_count). Times are UTC. For historical energy, sum power_rollups.energy_kwh at one resolution.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "One SELECT query, optionally with a WITH clause. Limit results to the facts needed for the answer."},
                },
                "required": ["sql"],
            },
        },
    },
]


_TOOLS = {
    "current_conditions": current_conditions,
    "energy_summary": energy_summary,
    "supply_voltage": supply_voltage,
    "collection_health": collection_health,
    "printer_activity": printer_activity,
    "query_telemetry": query_telemetry,
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
