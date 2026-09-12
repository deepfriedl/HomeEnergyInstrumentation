import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.getenv("ENERGY_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "home-energy.sqlite3"
LAN_CONFIG_PATH = Path(os.getenv("ENERGY_LAN_CONFIG", "config/lan.json"))


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


def admin_username() -> str:
    """Return the single allowed login name from private deployment config."""
    value = os.getenv("ENERGY_ADMIN_USERNAME", "").strip()
    if not value:
        raise RuntimeError("Set ENERGY_ADMIN_USERNAME in .env before starting the application.")
    return value


@contextmanager
def connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize():
    initial_devices = load_lan_devices()
    username = admin_username()
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS devices (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, ip_address TEXT NOT NULL UNIQUE,
          color TEXT NOT NULL DEFAULT '#64d8cb', enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          observed_at TEXT NOT NULL, watts REAL, voltage REAL, current REAL,
          total_kwh REAL, raw_json TEXT, error TEXT,
          UNIQUE(device_id, observed_at)
        );
        CREATE INDEX IF NOT EXISTS readings_device_time ON readings(device_id, observed_at);
        CREATE TABLE IF NOT EXISTS rollups (
          id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          resolution TEXT NOT NULL, bucket_start TEXT NOT NULL,
          avg_watts REAL, max_watts REAL, avg_voltage REAL, min_voltage REAL,
          max_voltage REAL, sample_count INTEGER NOT NULL,
          UNIQUE(device_id, resolution, bucket_start)
        );
        CREATE INDEX IF NOT EXISTS rollups_range ON rollups(resolution, bucket_start);
        CREATE TABLE IF NOT EXISTS hvac_readings (
          id INTEGER PRIMARY KEY,
          observed_at TEXT NOT NULL UNIQUE,
          indoor_temp_f REAL, indoor_humidity_pct REAL, outdoor_temp_f REAL,
          cooling_rate_pct REAL, heating_rate_pct REAL, blower_cfm REAL,
          system_mode TEXT, operation TEXT, fan_running INTEGER,
          aux_active INTEGER, defrost_active INTEGER, alert_count INTEGER,
          raw_json TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS hvac_readings_time ON hvac_readings(observed_at);
        CREATE TABLE IF NOT EXISTS hvac_rollups (
          id INTEGER PRIMARY KEY, resolution TEXT NOT NULL, bucket_start TEXT NOT NULL,
          avg_indoor_temp_f REAL, avg_indoor_humidity_pct REAL, avg_outdoor_temp_f REAL,
          avg_cooling_rate_pct REAL, max_cooling_rate_pct REAL,
          avg_heating_rate_pct REAL, max_heating_rate_pct REAL,
          avg_blower_cfm REAL, max_blower_cfm REAL, sample_count INTEGER NOT NULL,
          UNIQUE(resolution, bucket_start)
        );
        CREATE INDEX IF NOT EXISTS hvac_rollups_range ON hvac_rollups(resolution, bucket_start);
        CREATE TABLE IF NOT EXISTS weather_readings (
          id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL UNIQUE,
          station_id TEXT NOT NULL, source_observed_at TEXT,
          temperature_f REAL, humidity_pct REAL, dewpoint_f REAL,
          wind_mph REAL, wind_direction_degrees REAL, precipitation_last_hour_in REAL,
          conditions TEXT, raw_json TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS weather_readings_time ON weather_readings(observed_at);
        CREATE TABLE IF NOT EXISTS weather_rollups (
          id INTEGER PRIMARY KEY, resolution TEXT NOT NULL, bucket_start TEXT NOT NULL,
          avg_temperature_f REAL, avg_humidity_pct REAL, avg_dewpoint_f REAL,
          avg_wind_mph REAL, total_precipitation_in REAL, sample_count INTEGER NOT NULL,
          UNIQUE(resolution, bucket_start)
        );
        CREATE INDEX IF NOT EXISTS weather_rollups_range ON weather_rollups(resolution, bucket_start);
        """)
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            password = os.getenv("ENERGY_INITIAL_PASSWORD")
            if password:
                conn.execute("INSERT INTO users VALUES (?, ?, ?)", (username, hash_password(password), iso()))
        if initial_devices and not conn.execute("SELECT 1 FROM devices").fetchone():
            conn.executemany("INSERT INTO devices(name, ip_address, color, enabled, created_at) VALUES (?, ?, ?, ?, ?)", [
                (device["name"], device["ip_address"], device.get("color", "#64d8cb"), int(device.get("enabled", True)), iso())
                for device in initial_devices
            ])
    if not LAN_CONFIG_PATH.exists() and devices():
        write_lan_config()


def load_lan_devices():
    if not LAN_CONFIG_PATH.exists():
        return []
    try:
        loaded = json.loads(LAN_CONFIG_PATH.read_text(encoding="utf-8"))
        return [device for device in loaded.get("devices", []) if device.get("name") and device.get("ip_address")]
    except (OSError, json.JSONDecodeError):
        return []


def write_lan_config():
    LAN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"devices": [{key: device[key] for key in ("name", "ip_address", "color", "enabled")} for device in devices()]}
    temporary = LAN_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(LAN_CONFIG_PATH)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    _, rounds, salt, saved = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    return secrets.compare_digest(actual.hex(), saved)


def devices():
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM devices ORDER BY name")]


def enabled_devices():
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM devices WHERE enabled = 1 ORDER BY id")]


def save_device(name, ip_address, color, enabled, device_id=None):
    with connection() as conn:
        if device_id:
            conn.execute("UPDATE devices SET name=?, ip_address=?, color=?, enabled=? WHERE id=?", (name, ip_address, color, enabled, device_id))
        else:
            conn.execute("INSERT INTO devices(name, ip_address, color, enabled, created_at) VALUES (?, ?, ?, ?, ?)", (name, ip_address, color, enabled, iso()))
    write_lan_config()


def delete_device(device_id):
    with connection() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
    write_lan_config()


def save_reading(device_id, watts=None, voltage=None, current=None, total_kwh=None, payload=None, error=None, observed_at=None):
    with connection() as conn:
        conn.execute("""INSERT OR IGNORE INTO readings(device_id, observed_at, watts, voltage, current, total_kwh, raw_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (device_id, observed_at or iso(), watts, voltage, current, total_kwh, json.dumps(payload) if payload else None, error))


def save_hvac_reading(snapshot=None, error=None, observed_at=None):
    """Store one normalized S40 state snapshot alongside its structured payload."""
    snapshot = snapshot or {}
    with connection() as conn:
        conn.execute("""INSERT OR IGNORE INTO hvac_readings(
          observed_at, indoor_temp_f, indoor_humidity_pct, outdoor_temp_f,
          cooling_rate_pct, heating_rate_pct, blower_cfm, system_mode,
          operation, fan_running, aux_active, defrost_active, alert_count,
          raw_json, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            observed_at or iso(),
            snapshot.get("indoor_temp_f"), snapshot.get("indoor_humidity_pct"), snapshot.get("outdoor_temp_f"),
            snapshot.get("cooling_rate_pct"), snapshot.get("heating_rate_pct"), snapshot.get("blower_cfm"),
            snapshot.get("system_mode"), snapshot.get("operation"), snapshot.get("fan_running"),
            snapshot.get("aux_active"), snapshot.get("defrost_active"), snapshot.get("alert_count"),
            json.dumps(snapshot) if snapshot else None, error,
        ))


def save_weather_reading(snapshot=None, station_id=None, error=None, observed_at=None):
    """Store one normalized NWS observation and its source payload."""
    snapshot = snapshot or {}
    station_id = snapshot.get("station_id") or station_id
    if not station_id:
        return
    with connection() as conn:
        conn.execute("""INSERT OR IGNORE INTO weather_readings(
          observed_at, station_id, source_observed_at, temperature_f, humidity_pct,
          dewpoint_f, wind_mph, wind_direction_degrees, precipitation_last_hour_in,
          conditions, raw_json, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            observed_at or iso(), station_id, snapshot.get("source_observed_at"),
            snapshot.get("temperature_f"), snapshot.get("humidity_pct"), snapshot.get("dewpoint_f"),
            snapshot.get("wind_mph"), snapshot.get("wind_direction_degrees"),
            snapshot.get("precipitation_last_hour_in"), snapshot.get("conditions"),
            json.dumps(snapshot.get("raw")) if snapshot.get("raw") else None, error,
        ))


RANGES = {"24h": (24, "raw"), "7d": (168, "raw"), "30d": (720, "5m"), "6m": (4320, "hour"), "12m": (8760, "day"), "18m": (13152, "day")}


def overview(range_key="24h"):
    hours, resolution = RANGES.get(range_key, RANGES["24h"])
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        rows = conn.execute("""SELECT d.id, d.name, d.color, r.* FROM devices d
          LEFT JOIN readings r ON r.id=(SELECT id FROM readings WHERE device_id=d.id ORDER BY observed_at DESC LIMIT 1)
          ORDER BY d.name""").fetchall()
        if resolution == "raw":
            series = conn.execute("""SELECT observed_at, SUM(watts) watts, AVG(voltage) voltage, COUNT(watts) reporters
              FROM readings WHERE observed_at >= ? AND watts IS NOT NULL GROUP BY observed_at ORDER BY observed_at""", (cutoff,)).fetchall()
        else:
            series = conn.execute("""SELECT bucket_start AS observed_at, SUM(avg_watts) watts, AVG(avg_voltage) voltage, SUM(sample_count) reporters
              FROM rollups WHERE resolution=? AND bucket_start >= ? GROUP BY bucket_start ORDER BY bucket_start""", (resolution, cutoff)).fetchall()
    return [dict(row) for row in rows], [dict(row) for row in series]


def device_series(device_id, range_key="24h"):
    hours, resolution = RANGES.get(range_key, RANGES["24h"])
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        if resolution == "raw":
            rows = conn.execute("SELECT observed_at, watts, voltage FROM readings WHERE device_id=? AND observed_at>=? ORDER BY observed_at", (device_id, cutoff))
        else:
            rows = conn.execute("SELECT bucket_start AS observed_at, avg_watts AS watts, avg_voltage AS voltage FROM rollups WHERE device_id=? AND resolution=? AND bucket_start>=? ORDER BY bucket_start", (device_id, resolution, cutoff))
        return [dict(row) for row in rows]


def comparison_series(range_key="24h"):
    """Return per-device power series for the overview comparison chart."""
    hours, resolution = RANGES.get(range_key, RANGES["24h"])
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        if resolution == "raw":
            rows = conn.execute("""SELECT d.id, d.name, d.color, r.observed_at, r.watts
                FROM readings r JOIN devices d ON d.id=r.device_id
                WHERE r.observed_at>=? AND r.watts IS NOT NULL ORDER BY d.id, r.observed_at""", (cutoff,))
        else:
            rows = conn.execute("""SELECT d.id, d.name, d.color, r.bucket_start AS observed_at, r.avg_watts AS watts
                FROM rollups r JOIN devices d ON d.id=r.device_id
                WHERE r.resolution=? AND r.bucket_start>=? AND r.avg_watts IS NOT NULL ORDER BY d.id, r.bucket_start""", (resolution, cutoff))
        grouped = {}
        for row in rows:
            item = grouped.setdefault(row["id"], {"name": row["name"], "color": row["color"], "points": []})
            item["points"].append({"observed_at": row["observed_at"], "watts": row["watts"]})
    return list(grouped.values())


def hvac_latest():
    """Return the most recent Lennox state, including a collection error if present."""
    with connection() as conn:
        row = conn.execute("SELECT * FROM hvac_readings ORDER BY observed_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def weather_latest():
    with connection() as conn:
        row = conn.execute("SELECT * FROM weather_readings ORDER BY observed_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def hvac_series(range_key="24h"):
    """Return normalized S40 telemetry at the same retention resolution as plug data."""
    hours, resolution = RANGES.get(range_key, RANGES["24h"])
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        if resolution == "raw":
            rows = conn.execute("""SELECT observed_at, indoor_temp_f, indoor_humidity_pct,
              outdoor_temp_f, cooling_rate_pct, heating_rate_pct, blower_cfm,
              system_mode, operation, fan_running, aux_active, defrost_active,
              alert_count, error FROM hvac_readings WHERE observed_at >= ?
              ORDER BY observed_at""", (cutoff,)).fetchall()
        else:
            rows = conn.execute("""SELECT bucket_start AS observed_at,
              avg_indoor_temp_f AS indoor_temp_f,
              avg_indoor_humidity_pct AS indoor_humidity_pct,
              avg_outdoor_temp_f AS outdoor_temp_f,
              avg_cooling_rate_pct AS cooling_rate_pct,
              avg_heating_rate_pct AS heating_rate_pct,
              avg_blower_cfm AS blower_cfm,
              NULL AS system_mode, NULL AS operation, NULL AS fan_running,
              NULL AS aux_active, NULL AS defrost_active, NULL AS alert_count,
              NULL AS error FROM hvac_rollups WHERE resolution=? AND bucket_start >= ?
              ORDER BY bucket_start""", (resolution, cutoff)).fetchall()
    return [dict(row) for row in rows]


def climate_series(range_key="24h"):
    """HVAC series with the latest independent station observation carried forward."""
    rows = hvac_series(range_key)
    hours, resolution = RANGES.get(range_key, RANGES["24h"])
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        if resolution == "raw":
            weather_rows = conn.execute("SELECT observed_at, temperature_f, humidity_pct, dewpoint_f FROM weather_readings WHERE observed_at >= ? ORDER BY observed_at", (cutoff,)).fetchall()
        else:
            weather_rows = conn.execute("""SELECT bucket_start AS observed_at, avg_temperature_f AS temperature_f,
              avg_humidity_pct AS humidity_pct, avg_dewpoint_f AS dewpoint_f FROM weather_rollups
              WHERE resolution=? AND bucket_start >= ? ORDER BY bucket_start""", (resolution, cutoff)).fetchall()
    weather_rows = [dict(row) for row in weather_rows]
    index = 0
    current = None
    for row in rows:
        while index < len(weather_rows) and weather_rows[index]["observed_at"] <= row["observed_at"]:
            current = weather_rows[index]
            index += 1
        row["nws_temp_f"] = current["temperature_f"] if current else None
        row["nws_humidity_pct"] = current["humidity_pct"] if current else None
        row["nws_dewpoint_f"] = current["dewpoint_f"] if current else None
    return rows


def cleanup_and_rollup():
    """Create 5-minute/hourly/daily aggregates and remove expired raw payloads/minute readings."""
    with connection() as conn:
        for resolution, fmt in (("5m", "%Y-%m-%dT%H:%M:00+00:00"), ("hour", "%Y-%m-%dT%H:00:00+00:00"), ("day", "%Y-%m-%dT00:00:00+00:00")):
            # SQLite date functions make rollups portable and idempotent.
            conn.execute(f"""INSERT OR REPLACE INTO rollups(device_id,resolution,bucket_start,avg_watts,max_watts,avg_voltage,min_voltage,max_voltage,sample_count)
              SELECT device_id, ?, strftime(?, observed_at), AVG(watts), MAX(watts), AVG(voltage), MIN(voltage), MAX(voltage), COUNT(*)
              FROM readings GROUP BY device_id, strftime(?, observed_at)""", (resolution, fmt, fmt))
        for resolution, days in (("5m", 30), ("hour", 183), ("day", 548)):
            conn.execute("DELETE FROM rollups WHERE resolution=? AND bucket_start < ?", (resolution, iso(now()-timedelta(days=days))))
        conn.execute("UPDATE readings SET raw_json=NULL WHERE observed_at < ?", (iso(now()-timedelta(days=30)),))
        conn.execute("DELETE FROM readings WHERE observed_at < ?", (iso(now()-timedelta(days=7)),))
        for resolution, fmt in (("5m", "%Y-%m-%dT%H:%M:00+00:00"), ("hour", "%Y-%m-%dT%H:00:00+00:00"), ("day", "%Y-%m-%dT00:00:00+00:00")):
            conn.execute(f"""INSERT OR REPLACE INTO hvac_rollups(
              resolution, bucket_start, avg_indoor_temp_f, avg_indoor_humidity_pct,
              avg_outdoor_temp_f, avg_cooling_rate_pct, max_cooling_rate_pct,
              avg_heating_rate_pct, max_heating_rate_pct, avg_blower_cfm,
              max_blower_cfm, sample_count
            ) SELECT ?, strftime(?, observed_at), AVG(indoor_temp_f), AVG(indoor_humidity_pct),
              AVG(outdoor_temp_f), AVG(cooling_rate_pct), MAX(cooling_rate_pct),
              AVG(heating_rate_pct), MAX(heating_rate_pct), AVG(blower_cfm),
              MAX(blower_cfm), COUNT(*)
            FROM hvac_readings GROUP BY strftime(?, observed_at)""", (resolution, fmt, fmt))
        for resolution, days in (("5m", 30), ("hour", 183), ("day", 548)):
            conn.execute("DELETE FROM hvac_rollups WHERE resolution=? AND bucket_start < ?", (resolution, iso(now()-timedelta(days=days))))
        conn.execute("UPDATE hvac_readings SET raw_json=NULL WHERE observed_at < ?", (iso(now()-timedelta(days=30)),))
        conn.execute("DELETE FROM hvac_readings WHERE observed_at < ?", (iso(now()-timedelta(days=7)),))
        for resolution, fmt in (("5m", "%Y-%m-%dT%H:%M:00+00:00"), ("hour", "%Y-%m-%dT%H:00:00+00:00"), ("day", "%Y-%m-%dT00:00:00+00:00")):
            conn.execute(f"""INSERT OR REPLACE INTO weather_rollups(
              resolution, bucket_start, avg_temperature_f, avg_humidity_pct,
              avg_dewpoint_f, avg_wind_mph, total_precipitation_in, sample_count
            ) SELECT ?, strftime(?, observed_at), AVG(temperature_f), AVG(humidity_pct),
              AVG(dewpoint_f), AVG(wind_mph), SUM(precipitation_last_hour_in), COUNT(*)
            FROM weather_readings GROUP BY strftime(?, observed_at)""", (resolution, fmt, fmt))
        for resolution, days in (("5m", 30), ("hour", 183), ("day", 548)):
            conn.execute("DELETE FROM weather_rollups WHERE resolution=? AND bucket_start < ?", (resolution, iso(now()-timedelta(days=days))))
        conn.execute("UPDATE weather_readings SET raw_json=NULL WHERE observed_at < ?", (iso(now()-timedelta(days=30)),))
        conn.execute("DELETE FROM weather_readings WHERE observed_at < ?", (iso(now()-timedelta(days=7)),))
