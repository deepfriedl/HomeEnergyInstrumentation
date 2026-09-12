"""Idempotently import the existing JSON-lines Shelly collector log.

Usage: python -m app.import_logs /path/to/collector.log
"""
import json
import sys
from pathlib import Path

from app import db


def import_file(path: Path) -> tuple[int, int]:
    db.initialize()
    devices_by_ip = {device["ip_address"]: device for device in db.devices()}
    imported = skipped = 0
    # One transaction makes a months-long minute-by-minute history practical to import.
    with db.connection() as conn, path.open(encoding="utf-8") as log:
        for line_number, line in enumerate(log, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                device = devices_by_ip.get(record["ip"])
                if not device:
                    raise ValueError(f"unconfigured device IP {record['ip']}")
                status = record.get("status")
                conn.execute("""INSERT OR IGNORE INTO readings(device_id, observed_at, watts, voltage, current, total_kwh, raw_json, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                    device["id"], record["timestamp"],
                    status.get("apower") if status else None,
                    status.get("voltage") if status else None,
                    status.get("current") if status else None,
                    ((status.get("aenergy") or {}).get("total", 0) / 1000) if status else None,
                    json.dumps(status) if status else None, str(record.get("error")) if "error" in record else None,
                ))
                imported += 1
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                skipped += 1
                print(f"Skipped line {line_number}: {exc}", file=sys.stderr)
    db.cleanup_and_rollup()
    return imported, skipped


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.import_logs /path/to/collector.log")
    count, skipped = import_file(Path(sys.argv[1]))
    print(f"Imported {count} records; skipped {skipped} malformed/unconfigured records.")
