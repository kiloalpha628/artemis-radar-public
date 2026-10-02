"""Storm motion for storm-relative velocity, from forecast winds aloft.

Winds come from Open-Meteo (free, no key) at the radar's location for the hour
nearest the volume: 10 m and pressure levels whose height is between the ground
and 6 km above it. Storm motion is the Bunkers right-moving estimate: the
0–6 km mean wind plus 7.5 m/s perpendicular to the right of the 0–6 km shear,
the standard estimate for rotating (supercell) storms. When the shear is too
weak to define a direction, the mean wind is used.

Each forecast covers yesterday and today (48 hours), so it is fetched about once
an hour per radar and kept: if a refresh fails, the last forecast still answers
for the hours it covers. After a failure with nothing to fall back on, the next
attempt waits a few minutes rather than stalling every volume. Open-Meteo can
take tens of seconds on a first request, and the 600 hPa level was consistently
slow, so it is not requested (925, 850, 700, 500 and 400 hPa still span 0-6 km).
"""
import math
import threading
import time
from datetime import datetime, timedelta, timezone

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LEVELS = (925, 850, 700, 500, 400)
DEVIATION = 7.5  # m/s, Bunkers et al. (2000)
DEPTH = 6000.0  # m above ground
REFRESH = 3600  # s between forecast fetches per radar
RETRY = 600  # s before retrying after a failed fetch


def to_uv(speed, direction_from):
    """Meteorological wind (speed, direction it blows from, degrees) as (u east, v north)."""
    radians = math.radians(direction_from)
    return -speed * math.sin(radians), -speed * math.cos(radians)


def from_uv(u, v):
    """(speed, direction it moves from, degrees) for a vector (u, v)."""
    return math.hypot(u, v), (math.degrees(math.atan2(-u, -v)) + 360) % 360


def bunkers_right(winds):
    """Storm motion (u, v) from winds ordered bottom to top, each (u, v)."""
    if len(winds) < 2:
        raise ValueError("Need at least two wind levels")
    mean_u = sum(u for u, _ in winds) / len(winds)
    mean_v = sum(v for _, v in winds) / len(winds)
    shear_u = winds[-1][0] - winds[0][0]
    shear_v = winds[-1][1] - winds[0][1]
    magnitude = math.hypot(shear_u, shear_v)
    if magnitude < 1.0:
        return mean_u, mean_v
    # Rotate the shear 90° clockwise (to the right) and scale to the deviation.
    return mean_u + DEVIATION * shear_v / magnitude, mean_v - DEVIATION * shear_u / magnitude


def winds_from_forecast(document, when):
    """Winds (u, v) from 10 m up to 6 km above ground at the hour nearest ``when``."""
    hourly = document.get("hourly") or {}
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly.get("time") or []]
    if not times:
        raise ValueError("Forecast has no hours")
    index = min(range(len(times)), key=lambda i: abs(times[i] - when))
    if abs(times[index] - when) > timedelta(hours=2):
        raise ValueError("No forecast hour near the volume")
    ground = float(document.get("elevation") or 0.0)

    def at(name):
        values = hourly.get(name) or []
        return values[index] if index < len(values) else None

    winds = []
    speed, direction = at("wind_speed_10m"), at("wind_direction_10m")
    if speed is not None and direction is not None:
        winds.append(to_uv(speed, direction))
    for level in LEVELS:
        speed, direction, height = at(f"wind_speed_{level}hPa"), at(f"wind_direction_{level}hPa"), at(f"geopotential_height_{level}hPa")
        if None in (speed, direction, height) or not ground < height <= ground + DEPTH:
            continue
        winds.append(to_uv(speed, direction))
    return winds


class StormMotion:
    """Storm motion per radar and volume time; ``None`` when winds are unavailable."""

    def __init__(self, session=None, monotonic=time.monotonic):
        self.session = session
        self.monotonic = monotonic
        # (lat, lon) -> {"document": last good forecast or None, "fetched": s, "failed": s or None}
        self.forecasts = {}
        self.lock = threading.Lock()

    def __call__(self, lat, lon, when):
        try:
            document = self._forecast(round(lat, 2), round(lon, 2))
            if document is None:
                return None
            u, v = bunkers_right(winds_from_forecast(document, when))
        except Exception:
            return None
        speed, direction = from_uv(u, v)
        return {"u": round(u, 2), "v": round(v, 2), "speed": round(speed, 1),
                "direction_from": round(direction), "method": "Bunkers right-mover, 0-6 km, Open-Meteo winds"}

    def _forecast(self, lat, lon):
        """The forecast for a location: fetched when due, else the last good one."""
        now = self.monotonic()
        with self.lock:
            entry = self.forecasts.setdefault((lat, lon), {"document": None, "fetched": None, "failed": None})
            due = entry["fetched"] is None or now - entry["fetched"] >= REFRESH
            waiting = entry["failed"] is not None and now - entry["failed"] < RETRY
            if not due or waiting:
                return entry["document"]
        try:
            document = self._fetch(lat, lon)
        except Exception:
            with self.lock:
                entry["failed"] = now
                return entry["document"]
        with self.lock:
            entry.update(document=document, fetched=now, failed=None)
        return document

    def _fetch(self, lat, lon):
        import requests
        variables = ["wind_speed_10m", "wind_direction_10m"] + [
            f"{name}_{level}hPa" for level in LEVELS for name in ("wind_speed", "wind_direction", "geopotential_height")
        ]
        session = self.session or requests
        response = session.get(FORECAST_URL, timeout=(10, 60), params={
            "latitude": round(lat, 2), "longitude": round(lon, 2), "hourly": ",".join(variables),
            "wind_speed_unit": "ms", "past_days": 1, "forecast_days": 1, "timezone": "GMT",
        })
        response.raise_for_status()
        return response.json()
