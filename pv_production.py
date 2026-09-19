"""Godzinowa produkcja PV z PVGIS (z cache) lub profilu klimatycznego."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).with_name("uploads") / "pvgis_cache"
PVGIS_ENDPOINT = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
DEFAULT_RADDATABASE = "PVGIS-SARAH2"
DEFAULT_YEAR = 2016


def _cache_key(
    latitude: float,
    longitude: float,
    tilt_deg: float,
    azimuth_deg: float,
    system_loss_percent: float,
    year: int,
    raddatabase: str,
) -> str:
    payload = {
        "lat": round(latitude, 4),
        "lon": round(longitude, 4),
        "tilt": round(tilt_deg, 2),
        "azimuth": round(azimuth_deg, 2),
        "loss": round(system_loss_percent, 2),
        "year": year,
        "db": raddatabase,
        "peakpower": 1.0,
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"pvgis_{digest}.json"


def fetch_pvgis_hourly_per_kwp(
    latitude: float,
    longitude: float,
    tilt_deg: float = 35.0,
    azimuth_deg: float = 0.0,
    system_loss_percent: float = 14.0,
    year: int = DEFAULT_YEAR,
    raddatabase: str = DEFAULT_RADDATABASE,
    cache_dir: Path = CACHE_DIR,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Pobiera godzinową produkcję dla 1 kWp (kWh/h) z PVGIS seriescalc."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / _cache_key(
        latitude, longitude, tilt_deg, azimuth_deg, system_loss_percent, year, raddatabase
    )
    if use_cache and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return _hourly_frame_from_payload(payload)

    params = {
        "lat": latitude,
        "lon": longitude,
        "raddatabase": raddatabase,
        "browser": 0,
        "outputformat": "json",
        "usehorizon": 1,
        "angle": tilt_deg,
        "aspect": azimuth_deg,
        "startyear": year,
        "endyear": year,
        "pvcalculation": 1,
        "peakpower": 1,
        "loss": system_loss_percent,
        "trackingtype": 0,
        "components": 0,
    }
    url = f"{PVGIS_ENDPOINT}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PVGIS HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Nie udało się połączyć z PVGIS: {error}") from error

    if use_cache:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return _hourly_frame_from_payload(payload)


def _hourly_frame_from_payload(payload: dict) -> pd.DataFrame:
    rows = payload.get("outputs", {}).get("hourly") or []
    if not rows:
        raise RuntimeError("PVGIS nie zwrócił danych godzinowych.")

    frame = pd.DataFrame(rows)
    # time format: YYYYMMDD:HHMM (UTC)
    frame["timestamp"] = pd.to_datetime(frame["time"], format="%Y%m%d:%H%M", utc=True)
    # P jest mocą w W dla 1 kWp → energia godzinowa w kWh
    frame["kwh_per_kwp"] = pd.to_numeric(frame["P"], errors="coerce").fillna(0.0) / 1000.0
    frame["month"] = frame["timestamp"].dt.month
    frame["day"] = frame["timestamp"].dt.day
    frame["hour"] = frame["timestamp"].dt.hour
    frame["dayofyear"] = frame["timestamp"].dt.dayofyear
    return frame[["timestamp", "month", "day", "hour", "dayofyear", "kwh_per_kwp"]].copy()


def climatology_lookup(hourly_per_kwp: pd.DataFrame) -> pd.DataFrame:
    """Średnia produkcja 1 kWp wg (miesiąc, dzień, godzina) — typowy rok."""
    grouped = (
        hourly_per_kwp.groupby(["month", "day", "hour"], as_index=False)["kwh_per_kwp"]
        .mean()
        .rename(columns={"kwh_per_kwp": "kwh_per_kwp"})
    )
    return grouped


def production_for_timestamps(
    timestamps: pd.Series,
    hourly_per_kwp: pd.DataFrame,
    kwp: float,
    inverter_ac_kw: float | None = None,
) -> pd.Series:
    """Dopasowuje produkcję PV do znaczników czasu zużycia (po miesiącu/dniu/godzinie)."""
    if kwp < 0:
        raise ValueError("Moc instalacji kWp nie może być ujemna.")

    lookup = climatology_lookup(hourly_per_kwp)
    keys = pd.DataFrame({
        "month": timestamps.dt.month,
        "day": timestamps.dt.day,
        "hour": timestamps.dt.hour,
    })
    merged = keys.merge(lookup, on=["month", "day", "hour"], how="left")

    # 29 lutego / braki → średnia z tej samej godziny w miesiącu
    if merged["kwh_per_kwp"].isna().any():
        month_hour = (
            hourly_per_kwp.groupby(["month", "hour"])["kwh_per_kwp"].mean().reset_index()
        )
        fallback = keys.merge(month_hour, on=["month", "hour"], how="left")
        merged["kwh_per_kwp"] = merged["kwh_per_kwp"].fillna(fallback["kwh_per_kwp"])

    production = merged["kwh_per_kwp"].fillna(0.0).to_numpy() * float(kwp)
    if inverter_ac_kw is not None and inverter_ac_kw > 0:
        production = production.clip(max=float(inverter_ac_kw))
    return pd.Series(production, index=timestamps.index, name="production_kwh")


def synthetic_hourly_per_kwp(year: int = 2024) -> pd.DataFrame:
    """Awaryjny profil PL gdy PVGIS jest niedostępny (~1000 kWh/kWp/rok)."""
    import math

    index = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h", tz="UTC")
    hour = index.hour.to_numpy()
    dayofyear = index.dayofyear.to_numpy()
    daylight = ((hour >= 5) & (hour <= 20)).astype(float)
    solar_shape = daylight * (1.0 - abs(hour - 12) / 8.0).clip(min=0.0)
    seasonal = [
        0.45 + 0.55 * (0.5 - 0.5 * math.cos(2 * math.pi * (day - 172) / 365.0))
        for day in dayofyear
    ]
    raw = solar_shape * seasonal
    total = raw.sum()
    if total > 0:
        raw = raw / total * 1000.0
    return pd.DataFrame({
        "timestamp": index,
        "month": index.month,
        "day": index.day,
        "hour": index.hour,
        "dayofyear": index.dayofyear,
        "kwh_per_kwp": raw,
    })
