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
    with path.open(encoding="utf-8") as log:
        for line_number, line in enumerate(log, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                device = devices_by_ip.get(record["ip"])
                status = record["status"]
                if not device:
                    raise ValueError(f"unconfigured device IP {record['ip']}")
                db.save_reading(
                    device_id=device["id"], observed_at=record["timestamp"],
                    watts=status.get("apower"), voltage=status.get("voltage"),
                    current=status.get("current"),
                    total_kwh=(status.get("aenergy") or {}).get("total", 0) / 1000,
                    payload=status,
                )
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
