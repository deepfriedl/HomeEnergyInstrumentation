#!/usr/bin/env python3
"""Interactively apply the Home Energy baseline to one Shelly Plug US Gen4.

This tool intentionally changes only the selected plug.  It never stores a
password, toggles the relay, changes the station Wi-Fi settings, or updates
firmware.  A local pre-change backup is written before any configuration is
modified.
"""

import ipaddress
import json
import hashlib
from getpass import getpass
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


BACKUP_DIR = Path("shelly-backups")
TIMEOUT_SECONDS = 8


class ShellyError(RuntimeError):
    """A local Shelly RPC request failed."""


def rpc(host, method, params=None, password=None):
    """Send one JSON-RPC request to a Gen2+ Shelly device."""
    payload = {"id": 1, "method": method}
    if params:
        payload["params"] = params
    url_host = f"[{host}]" if ":" in host else host
    request = urllib.request.Request(
        f"http://{url_host}/rpc",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        if password:
            passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            passwords.add_password(None, f"http://{url_host}/", "admin", password)
            opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(passwords))
            response_context = opener.open(request, timeout=TIMEOUT_SECONDS)
        else:
            response_context = urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
        with response_context as response:
            reply = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ShellyError(f"{method}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ShellyError(f"{method}: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
    except json.JSONDecodeError as exc:
        raise ShellyError(f"{method}: invalid JSON response") from exc
    if "error" in reply:
        detail = reply["error"].get("message", reply["error"])
        raise ShellyError(f"{method}: {detail}")
    return reply.get("result", {})


def nested(value, *keys, default=None):
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return value if value is not None else default


def prompt_host():
    while True:
        raw = input("Shelly IP address: ").strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            print("Enter a valid IPv4 or IPv6 address.")


def prompt_name():
    while True:
        name = input("Friendly name: ").strip()
        if name:
            return name
        print("A friendly name is required.")


def prompt_password():
    while True:
        password = getpass("Shared Shelly password (input hidden): ")
        if not password:
            print("A password is required to enable local authentication.")
            continue
        confirmation = getpass("Repeat shared Shelly password: ")
        if password == confirmation:
            return password
        print("Passwords did not match. Try again.")


def read_snapshot(host, password=None):
    """Read the configuration needed for review, backup, and verification."""
    return {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "device_info": rpc(host, "Shelly.GetDeviceInfo", password=password),
        "system": rpc(host, "Sys.GetConfig", password=password),
        "wifi": rpc(host, "WiFi.GetConfig", password=password),
        "wifi_status": rpc(host, "WiFi.GetStatus", password=password),
        "switch": rpc(host, "Switch.GetConfig", {"id": 0}, password=password),
    }


def print_review(host, name, snapshot):
    info = snapshot["device_info"]
    switch = snapshot["switch"]
    wifi = snapshot["wifi"]
    wifi_status = snapshot["wifi_status"]
    print("\nCurrent configuration")
    print(f"  Address:       {host}")
    print(f"  Model:         {info.get('model', 'unknown')}")
    print(f"  Firmware:      {info.get('fw_id', 'unknown')}")
    print(f"  Station Wi-Fi: {wifi_status.get('status', 'unknown')} ({wifi_status.get('sta_ip', 'no IP')})")
    print(f"  Device name:   {nested(snapshot['system'], 'device', 'name', default='(unset)')}")
    print(f"  Switch name:   {switch.get('name') or '(unset)'}")
    print(f"  Power on:      {switch.get('initial_state', 'unknown')}")
    print(f"  Shelly AP:     {'enabled' if nested(wifi, 'ap', 'enable', default=False) else 'disabled'}")
    print("\nProposed baseline")
    print(f"  Device and switch name: {name}")
    print("  Power-on state:         on")
    print("  Shelly access point:    disabled")
    print("  Local authentication:   enabled")
    print("  No relay, station Wi-Fi, firmware, cloud, or radio settings will change.")


def require_station_connection(host, snapshot):
    """Refuse to remove the AP unless the plug is reachable through station Wi-Fi."""
    status = snapshot["wifi_status"].get("status")
    station_ip = snapshot["wifi_status"].get("sta_ip")
    if status != "got ip" or station_ip != host:
        raise ShellyError(
            "refusing to disable the Shelly AP because station Wi-Fi is not "
            f"confirmed at this address (status={status!r}, sta_ip={station_ip!r})"
        )


def write_backup(host, snapshot):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"{host.replace(':', '_')}-{timestamp}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def apply_baseline(host, name, password, device_id):
    rpc(host, "Sys.SetConfig", {"config": {"device": {"name": name}}}, password=password)
    rpc(host, "Switch.SetConfig", {"id": 0, "config": {"name": name, "initial_state": "on"}}, password=password)
    # Keep this last: it changes only the unused Shelly AP, not station Wi-Fi.
    rpc(host, "WiFi.SetConfig", {"config": {"ap": {"enable": False}}}, password=password)
    ha1 = hashlib.sha256(f"admin:{device_id}:{password}".encode("utf-8")).hexdigest()
    rpc(host, "Shelly.SetAuth", {"user": "admin", "realm": device_id, "ha1": ha1}, password=password)


def verify(host, name, password):
    """Re-read after applying, allowing a short Wi-Fi configuration settle time."""
    last_error = None
    for _ in range(5):
        try:
            snapshot = read_snapshot(host, password)
            switch = snapshot["switch"]
            valid = (
                nested(snapshot["system"], "device", "name") == name
                and switch.get("name") == name
                and switch.get("initial_state") == "on"
                and nested(snapshot["wifi"], "ap", "enable", default=True) is False
            )
            if valid:
                return snapshot
            last_error = ShellyError("verification returned an unexpected configuration")
        except ShellyError as exc:
            last_error = exc
        time.sleep(1)
    raise last_error or ShellyError("verification failed")


def main():
    print("Shelly Plug normalizer — one plug per run")
    host = prompt_host()
    name = prompt_name()
    password = prompt_password()
    try:
        snapshot = read_snapshot(host, password)
        require_station_connection(host, snapshot)
    except ShellyError as exc:
        print(f"\nCould not read {host}: {exc}", file=sys.stderr)
        return 1

    print_review(host, name, snapshot)
    if input("\nType APPLY to make these changes: ").strip() != "APPLY":
        print("No changes made.")
        return 0

    try:
        backup = write_backup(host, snapshot)
        device_id = snapshot["device_info"].get("id")
        if not device_id:
            raise ShellyError("device did not report its identifier; cannot configure authentication")
        apply_baseline(host, name, password, device_id)
        verify(host, name, password)
    except (OSError, ShellyError) as exc:
        print(f"\nNormalization did not complete: {exc}", file=sys.stderr)
        print("The pre-change backup is retained if it was created.", file=sys.stderr)
        return 1

    print(f"\nSuccess. Verified {name!r} at {host}, with local authentication enabled.")
    print(f"Pre-change backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
