import asyncio
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request

from app import db


SHELLY_USERNAME = "admin"
SHELLY_PASSWORD = os.getenv("ENERGY_SHELLY_PASSWORD", "")


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


def digest_authorization(method, uri, password, challenge):
    """Build Shelly's SHA-256 Digest response without Python-version reliance."""
    if not challenge or not challenge.lower().startswith("digest "):
        raise RuntimeError("device did not provide a Digest authentication challenge")
    values = urllib.request.parse_keqv_list(urllib.request.parse_http_list(challenge[7:]))
    realm = values.get("realm")
    nonce = values.get("nonce")
    algorithm = values.get("algorithm", "MD5").upper()
    qop = values.get("qop", "auth")
    if not realm or not nonce or algorithm != "SHA-256" or "auth" not in qop.split(","):
        raise RuntimeError("device returned an unsupported Digest authentication challenge")
    nc = "00000001"
    cnonce = secrets.token_hex(16)
    ha1 = hashlib.sha256(f"{SHELLY_USERNAME}:{realm}:{password}".encode("utf-8")).hexdigest()
    ha2 = hashlib.sha256(f"{method}:{uri}".encode("utf-8")).hexdigest()
    response = hashlib.sha256(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode("utf-8")).hexdigest()
    return (
        "Digest "
        f'username="{SHELLY_USERNAME}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
        f'response="{response}", algorithm=SHA-256, qop=auth, nc={nc}, cnonce="{cnonce}"'
    )


def fetch_url(url, endpoint):
    """Fetch a Shelly endpoint, retrying once with local Digest authentication."""
    request = urllib.request.Request(url, method="GET")
    try:
        return urllib.request.urlopen(request, timeout=8)
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or not SHELLY_PASSWORD:
            raise
        challenge = next((item for item in exc.headers.get_all("WWW-Authenticate", []) if item.lower().startswith("digest ")), None)
        authorization = digest_authorization("GET", f"/{endpoint}", SHELLY_PASSWORD, challenge)
        return urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": authorization}, method="GET"),
            timeout=8,
        )


def fetch(device):
    errors = []
    for endpoint in ("rpc/Switch.GetStatus?id=0", "status"):
        try:
            url = f"http://{device['ip_address']}/{endpoint}"
            response_context = fetch_url(url, endpoint)
            with response_context as response:
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
