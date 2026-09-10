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


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


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
        """)
        if not conn.execute("SELECT 1 FROM users WHERE username = 'admin'").fetchone():
            password = os.getenv("ENERGY_INITIAL_PASSWORD")
            if password:
                conn.execute("INSERT INTO users VALUES (?, ?, ?)", ("admin", hash_password(password), iso()))
        if not conn.execute("SELECT 1 FROM devices").fetchone():
            conn.executemany("INSERT INTO devices(name, ip_address, color, enabled, created_at) VALUES (?, ?, ?, 1, ?)", [
                ("Refrigerator", "192.168.1.101", "#64d8cb", iso()),
                ("Clothes washer", "192.168.1.102", "#91a7ff", iso()),
            ])


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


def delete_device(device_id):
    with connection() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))


def save_reading(device_id, watts=None, voltage=None, current=None, total_kwh=None, payload=None, error=None):
    with connection() as conn:
        conn.execute("""INSERT INTO readings(device_id, observed_at, watts, voltage, current, total_kwh, raw_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (device_id, iso(), watts, voltage, current, total_kwh, json.dumps(payload) if payload else None, error))


def overview(hours=24):
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        rows = conn.execute("""SELECT d.id, d.name, d.color, r.* FROM devices d
          LEFT JOIN readings r ON r.id=(SELECT id FROM readings WHERE device_id=d.id ORDER BY observed_at DESC LIMIT 1)
          ORDER BY d.name""").fetchall()
        series = conn.execute("""SELECT observed_at, SUM(watts) watts, AVG(voltage) voltage, COUNT(watts) reporters
          FROM readings WHERE observed_at >= ? AND watts IS NOT NULL GROUP BY observed_at ORDER BY observed_at""", (cutoff,)).fetchall()
    return [dict(row) for row in rows], [dict(row) for row in series]


def device_series(device_id, hours=24):
    cutoff = iso(now() - timedelta(hours=hours))
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT observed_at, watts, voltage FROM readings WHERE device_id=? AND observed_at>=? ORDER BY observed_at", (device_id, cutoff))]


def cleanup_and_rollup():
    """Create 5-minute/hourly/daily aggregates and remove expired raw payloads/minute readings."""
    with connection() as conn:
        for resolution, fmt, start in (("5m", "%Y-%m-%dT%H:%M:00+00:00", now()-timedelta(days=30)), ("hour", "%Y-%m-%dT%H:00:00+00:00", now()-timedelta(days=180)), ("day", "%Y-%m-%dT00:00:00+00:00", now()-timedelta(days=548))):
            # SQLite date functions make rollups portable and idempotent.
            modifier = "-" + ("5 minutes" if resolution == "5m" else "1 hour" if resolution == "hour" else "1 day")
            conn.execute(f"""INSERT OR REPLACE INTO rollups(device_id,resolution,bucket_start,avg_watts,max_watts,avg_voltage,min_voltage,max_voltage,sample_count)
              SELECT device_id, ?, strftime(?, observed_at), AVG(watts), MAX(watts), AVG(voltage), MIN(voltage), MAX(voltage), COUNT(*)
              FROM readings WHERE observed_at < ? GROUP BY device_id, strftime(?, observed_at)""", (resolution, fmt, iso(start), fmt))
        conn.execute("UPDATE readings SET raw_json=NULL WHERE observed_at < ?", (iso(now()-timedelta(days=30)),))
        conn.execute("DELETE FROM readings WHERE observed_at < ?", (iso(now()-timedelta(days=7)),))
