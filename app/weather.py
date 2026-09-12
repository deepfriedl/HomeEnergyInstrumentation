"""Read-only collector for the configured National Weather Service station."""

import asyncio
import json
import os
import time
import urllib.request

from app import db


NWS_API = "https://api.weather.gov/stations/{station}/observations/latest"
POLL_SECONDS = 10 * 60


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def celsius_to_fahrenheit(value):
    value = number(value)
    return value * 9 / 5 + 32 if value is not None else None


def kilometers_to_miles(value):
    value = number(value)
    return value * 0.621371 if value is not None else None


def millimeters_to_inches(value):
    value = number(value)
    return value / 25.4 if value is not None else None


def fetch(station):
    """Fetch one NWS GeoJSON observation without sending home-specific data."""
    request = urllib.request.Request(
        NWS_API.format(station=station),
        headers={"User-Agent": "home-energy-instrumentation (local weather display)"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def snapshot(payload, station):
    properties = payload.get("properties", {})
    return {
        "station_id": station,
        "source_observed_at": properties.get("timestamp"),
        "temperature_f": celsius_to_fahrenheit((properties.get("temperature") or {}).get("value")),
        "humidity_pct": number((properties.get("relativeHumidity") or {}).get("value")),
        "dewpoint_f": celsius_to_fahrenheit((properties.get("dewpoint") or {}).get("value")),
        "wind_mph": kilometers_to_miles((properties.get("windSpeed") or {}).get("value")),
        "wind_direction_degrees": number((properties.get("windDirection") or {}).get("value")),
        "precipitation_last_hour_in": millimeters_to_inches((properties.get("precipitationLastHour") or {}).get("value")),
        "conditions": properties.get("textDescription"),
        "raw": properties,
    }


class WeatherCollector:
    """Poll a configured NWS station at most once per ten minutes."""

    def __init__(self):
        self.station = os.getenv("ENERGY_NWS_STATION", "").strip().upper()
        self.last_attempt = None

    async def collect_once(self):
        if not self.station:
            return
        current = time.monotonic()
        if self.last_attempt is not None and current - self.last_attempt < POLL_SECONDS:
            return
        self.last_attempt = current
        try:
            payload = await asyncio.to_thread(fetch, self.station)
            db.save_weather_reading(snapshot(payload, self.station))
        except Exception as exc:
            db.save_weather_reading(station_id=self.station, error=str(exc))
