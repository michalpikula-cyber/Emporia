"""Ocena zasadności instalacji PV na podstawie zużycia Emporia."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import holidays
import numpy as np
import pandas as pd

from battery_simulator import BatteryConfig, simulate_pv_battery
from pv_production import (
    fetch_pvgis_hourly_per_kwp,
    production_for_timestamps,
    synthetic_hourly_per_kwp,
)

VAT_ENERGY = 1.23  # VAT na energię elektryczną (rachunki)
VAT_PV_INSTALL = 1.08  # preferencyjne 8% VAT na domowy montaż PV / magazyn na dachu
DEFAULT_CONFIG_FILE = Path(__file__).with_name("pv_config.json")
DEFAULT_PRICES_FILE = Path(__file__).with_name("tariff_prices.json")
PHASE_COLUMNS = [
    "DomPik-Mains_A (kWhs)",
    "DomPik-Mains_B (kWhs)",
    "DomPik-Mains_C (kWhs)",
]


@dataclass
class PvSiteConfig:
    latitude: float = 50.06
    longitude: float = 19.94
    tilt_deg: float = 35.0
    azimuth_deg: float = 0.0
    system_loss_percent: float = 14.0
    kwp_scenarios: list[float] = field(default_factory=lambda: [5.0, 8.0, 10.0])
    export_price_net_per_kwh: float = 0.25
    capex_net_per_kwp: float = 4000.0
    battery_capex_net: float = 15000.0
    opex_net_per_year: float = 200.0
    degradation_percent_per_year: float = 0.5
    horizon_years: int = 20
    discount_rate_percent: float = 5.0
    tariff: str = "G11"
    inverter_ac_kw: float | None = None
    battery_enabled: bool = False
    battery_capacity_kwh: float = 10.0
    battery_charge_power_kw: float = 5.0
    battery_discharge_power_kw: float = 5.0
    battery_round_trip_efficiency: float = 0.9
    battery_degradation_cost_net_per_kwh: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PvSiteConfig":
        battery = data.get("battery") or {}
        return cls(
            latitude=float(data.get("latitude", 50.06)),
            longitude=float(data.get("longitude", 19.94)),
            tilt_deg=float(data.get("tilt_deg", 35.0)),
            azimuth_deg=float(data.get("azimuth_deg", 0.0)),
            system_loss_percent=float(data.get("system_loss_percent", 14.0)),
            kwp_scenarios=[float(value) for value in data.get("kwp_scenarios", [5.0, 8.0, 10.0])],
            export_price_net_per_kwh=float(data.get("export_price_net_per_kwh", 0.25)),
            capex_net_per_kwp=float(data.get("capex_net_per_kwp", 4000.0)),
            battery_capex_net=float(
                data.get(
                    "battery_capex_net",
                    battery.get("capex_net", 15000.0),
                )
            ),
            opex_net_per_year=float(data.get("opex_net_per_year", 200.0)),
            degradation_percent_per_year=float(data.get("degradation_percent_per_year", 0.5)),
            horizon_years=int(data.get("horizon_years", 20)),
            discount_rate_percent=float(data.get("discount_rate_percent", 5.0)),
            tariff=str(data.get("tariff", "G11")),
            inverter_ac_kw=(
                None if data.get("inverter_ac_kw") in (None, "", "null")
                else float(data["inverter_ac_kw"])
            ),
            battery_enabled=bool(battery.get("enabled", False)),
            battery_capacity_kwh=float(battery.get("capacity_kwh", 10.0)),
            battery_charge_power_kw=float(battery.get("charge_power_kw", 5.0)),
            battery_discharge_power_kw=float(battery.get("discharge_power_kw", 5.0)),
            battery_round_trip_efficiency=float(battery.get("round_trip_efficiency", 0.9)),
            battery_degradation_cost_net_per_kwh=float(
                battery.get("degradation_cost_net_per_kwh", 0.0)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "tilt_deg": self.tilt_deg,
            "azimuth_deg": self.azimuth_deg,
            "system_loss_percent": self.system_loss_percent,
            "kwp_scenarios": self.kwp_scenarios,
            "export_price_net_per_kwh": self.export_price_net_per_kwh,
            "capex_net_per_kwp": self.capex_net_per_kwp,
            "battery_capex_net": self.battery_capex_net,
            "opex_net_per_year": self.opex_net_per_year,
            "degradation_percent_per_year": self.degradation_percent_per_year,
            "horizon_years": self.horizon_years,
            "discount_rate_percent": self.discount_rate_percent,
            "tariff": self.tariff,
            "inverter_ac_kw": self.inverter_ac_kw,
            "battery": {
                "enabled": self.battery_enabled,
                "capacity_kwh": self.battery_capacity_kwh,
                "charge_power_kw": self.battery_charge_power_kw,
                "discharge_power_kw": self.battery_discharge_power_kw,
                "round_trip_efficiency": self.battery_round_trip_efficiency,
                "degradation_cost_net_per_kwh": self.battery_degradation_cost_net_per_kwh,
                "capex_net": self.battery_capex_net,
            },
        }


def load_pv_config(path: Path = DEFAULT_CONFIG_FILE) -> PvSiteConfig:
    if not path.exists():
        return PvSiteConfig()
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    return PvSiteConfig.from_dict(data)


def save_pv_config(config: PvSiteConfig, path: Path = DEFAULT_CONFIG_FILE) -> None:
    existing: dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as source:
            existing = json.load(source)
    payload = config.to_dict()
    if "notes" in existing:
        payload["notes"] = existing["notes"]
    with path.open("w", encoding="utf-8") as target:
        json.dump(payload, target, indent=2, ensure_ascii=False)


def load_prices(path: Path = DEFAULT_PRICES_FILE) -> dict[str, float]:
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    return {key: float(value) for key, value in data["prices"].items()}


def polish_holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("PL", years=year)


def is_holiday(timestamp: pd.Timestamp) -> bool:
    return timestamp.date() in polish_holidays(timestamp.year)


def load_usage(
    path: Path,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(
        frame["Time Bucket (Europe/Budapest)"],
        format="%m/%d/%Y %H:%M:%S",
    )
    for column in PHASE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(
            frame[column].astype(str).str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0.0)
    frame["usage_kwh"] = frame[PHASE_COLUMNS].sum(axis=1)
    if start_date:
        frame = frame[frame["timestamp"] >= pd.to_datetime(start_date)]
    if end_date:
        frame = frame[frame["timestamp"] < pd.to_datetime(end_date) + pd.Timedelta(days=1)]
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        raise ValueError("Brak danych zużycia w wybranym zakresie.")
    return frame


def g13_zone(timestamp: pd.Timestamp) -> str:
    if timestamp.weekday() >= 5 or is_holiday(timestamp):
        return "reszta"
    if 7 <= timestamp.hour < 13:
        return "przed"
    if timestamp.month in range(4, 10) and 19 <= timestamp.hour < 22:
        return "po"
    if timestamp.month not in range(4, 10) and 16 <= timestamp.hour < 21:
        return "po"
    return "reszta"


def g13s_zone(timestamp: pd.Timestamp) -> str:
    month = timestamp.month
    hour = timestamp.hour
    is_summer = month in range(4, 10)
    is_workday = timestamp.weekday() < 5 and not is_holiday(timestamp)
    season = "lato" if is_summer else "zima"
    day_type = "roboczy" if is_workday else "wolny"

    if hour >= 21 or hour < 7:
        return f"{season}_{day_type}_noc"
    if is_summer:
        if 9 <= hour < 17:
            return f"{season}_{day_type}_szczyt"
        return f"{season}_{day_type}_pozaszczyt"
    if 10 <= hour < 15:
        return f"{season}_{day_type}_szczyt"
    if not is_workday and hour >= 15:
        return f"{season}_{day_type}_szczyt_wieczor"
    return f"{season}_{day_type}_pozaszczyt"


def variable_import_price_net(frame: pd.DataFrame, prices: dict[str, float], tariff: str) -> pd.Series:
    tariff = tariff.upper()
    additional = (
        prices["stawka_jakosciowa"] + prices["oplata_oze"] + prices["oplata_kogeneracyjna"]
    )
    if tariff == "G11":
        return pd.Series(
            prices["g11_energy"] + prices["g11_ss"] + additional,
            index=frame.index,
        )

    if tariff == "G12":
        is_night = frame["timestamp"].dt.hour.map(
            lambda hour: hour >= 22 or hour < 6 or 13 <= hour < 15
        )
        energy = np.where(is_night, prices["g12_energy_night"], prices["g12_energy_day"])
        network = np.where(is_night, prices["g12_ss_night"], prices["g12_ss_day"])
        return pd.Series(energy + network + additional, index=frame.index)

    if tariff == "G12W":
        is_cheap = frame["timestamp"].map(
            lambda stamp: (
                stamp.hour >= 22 or stamp.hour < 6 or 13 <= stamp.hour < 15
                or stamp.weekday() >= 5
                or is_holiday(stamp)
            )
        )
        energy = np.where(is_cheap, prices["g12w_energy_night"], prices["g12w_energy_day"])
        network = np.where(is_cheap, prices["g12w_ss_night"], prices["g12w_ss_day"])
        return pd.Series(energy + network + additional, index=frame.index)

    if tariff == "G13":
        zones = frame["timestamp"].map(g13_zone)
        energy = zones.map({
            "przed": prices["g13_energy_przed"],
            "po": prices["g13_energy_po"],
            "reszta": prices["g13_energy_reszta"],
        })
        network = zones.map({
            "przed": prices["g13_ss_przed"],
            "po": prices["g13_ss_po"],
            "reszta": prices["g13_ss_reszta"],
        })
        return energy + network + additional

    if tariff == "G13S":
        zones = frame["timestamp"].map(g13s_zone)
        energy = zones.map(lambda zone: prices[f"g13s_energy_{zone}"])
        network = zones.map(lambda zone: prices[f"g13s_ss_{zone}"])
        return energy + network + additional

    raise ValueError(f"Nieobsługiwana taryfa: {tariff}")


def fixed_monthly_net(prices: dict[str, float], tariff: str) -> float:
    tariff = tariff.upper()
    if tariff == "G13S":
        return (
            prices["skladnik_staly_ss"]
            + prices["oplata_handlowa_g13s"]
            + prices["oplata_mocowa"]
        )
    return (
        prices["skladnik_staly_ss"]
        + prices["oplata_abonamentowa"]
        + prices["oplata_mocowa"]
    )


def match_hourly(
    usage_kwh: np.ndarray,
    production_kwh: np.ndarray,
) -> dict[str, np.ndarray]:
    autoconsumed = np.minimum(usage_kwh, production_kwh)
    return {
        "autoconsumed_kwh": autoconsumed,
        "import_kwh": usage_kwh - autoconsumed,
        "export_kwh": production_kwh - autoconsumed,
    }


def annualize_factor(period_days: int) -> float:
    return 365.25 / max(period_days, 1)


def simple_payback_years(capex_gross: float, annual_savings_gross: float) -> float | None:
    if annual_savings_gross <= 0:
        return None
    return round(capex_gross / annual_savings_gross, 2)


def npv(
    capex_net: float,
    annual_cashflows_net: list[float],
    discount_rate_percent: float,
) -> float:
    rate = discount_rate_percent / 100.0
    total = -capex_net
    for year, cashflow in enumerate(annual_cashflows_net, start=1):
        total += cashflow / ((1 + rate) ** year)
    return total


def evaluate_kwp(
    frame: pd.DataFrame,
    production_kwh: pd.Series,
    prices: dict[str, float],
    config: PvSiteConfig,
    kwp: float,
) -> dict[str, Any]:
    usage = frame["usage_kwh"].to_numpy(dtype=float)
    production = production_kwh.to_numpy(dtype=float)
    import_price = variable_import_price_net(frame, prices, config.tariff).to_numpy(dtype=float)
    matched = match_hourly(usage, production)
    battery_stats: dict[str, Any] | None = None

    if config.battery_enabled and config.battery_capacity_kwh > 0:
        efficiency = max(config.battery_round_trip_efficiency, 1e-6) ** 0.5
        battery_config = BatteryConfig(
            capacity_kwh=config.battery_capacity_kwh,
            charge_power_kw=config.battery_charge_power_kw,
            discharge_power_kw=config.battery_discharge_power_kw,
            charge_efficiency=efficiency,
            discharge_efficiency=efficiency,
            degradation_cost_net_per_kwh=config.battery_degradation_cost_net_per_kwh,
        )
        battery_result = simulate_pv_battery(
            usage_kwh=usage,
            production_kwh=production,
            import_price_net=import_price,
            export_price_net=config.export_price_net_per_kwh,
            config=battery_config,
        )
        autoconsumed = float(
            battery_result["autoconsumed_direct_kwh"] + battery_result["discharged_to_load_kwh"]
        )
        grid_import = float(battery_result["grid_import_kwh"])
        grid_export = float(battery_result["grid_export_kwh"])
        avoided_import_value_net = float(battery_result["avoided_import_value_net"])
        export_value_net = float(battery_result["export_value_net"])
        residual_variable_net = float(battery_result["grid_import_cost_net"])
        degradation_cost_net = float(battery_result["degradation_cost_net"])
        profile_autoconsumed = matched["autoconsumed_kwh"]
        battery_stats = {
            "charged_from_pv_kwh": round(float(battery_result["charged_from_pv_kwh"]), 2),
            "discharged_to_load_kwh": round(float(battery_result["discharged_to_load_kwh"]), 2),
            "cycles_equivalent": round(float(battery_result["cycles_equivalent"]), 2),
            "final_soc_kwh": round(float(battery_result["final_soc_kwh"]), 2),
        }
    else:
        autoconsumed = float(matched["autoconsumed_kwh"].sum())
        grid_import = float(matched["import_kwh"].sum())
        grid_export = float(matched["export_kwh"].sum())
        avoided_import_value_net = float((matched["autoconsumed_kwh"] * import_price).sum())
        export_value_net = float(matched["export_kwh"].sum() * config.export_price_net_per_kwh)
        residual_variable_net = float((matched["import_kwh"] * import_price).sum())
        degradation_cost_net = 0.0
        profile_autoconsumed = matched["autoconsumed_kwh"]

    baseline_variable_net = float((usage * import_price).sum())
    period_days = int((frame["timestamp"].max() - frame["timestamp"].min()).days + 1)
    months = max(1, round(period_days / 30.44))
    fixed_net = fixed_monthly_net(prices, config.tariff) * months

    baseline_total_net = baseline_variable_net + fixed_net
    with_pv_total_net = residual_variable_net + degradation_cost_net + fixed_net
    period_bill_savings_net = baseline_total_net - with_pv_total_net

    scale = annualize_factor(period_days)
    annual_production = float(production.sum()) * scale
    annual_usage = float(usage.sum()) * scale
    annual_autoconsumed = autoconsumed * scale
    annual_export = grid_export * scale
    annual_import = grid_import * scale
    annual_bill_savings_net = period_bill_savings_net * scale
    annual_export_value_net = export_value_net * scale
    annual_opex_net = config.opex_net_per_year
    annual_net_benefit_year1 = annual_bill_savings_net + annual_export_value_net - annual_opex_net

    capex_pv_net = config.capex_net_per_kwp * kwp
    capex_battery_net = (
        float(config.battery_capex_net)
        if config.battery_enabled and config.battery_capacity_kwh > 0
        else 0.0
    )
    capex_net = capex_pv_net + capex_battery_net
    capex_gross = capex_net * VAT_PV_INSTALL
    annual_benefit_gross = annual_net_benefit_year1 * VAT_ENERGY
    degradation = config.degradation_percent_per_year / 100.0
    cashflows = [
        (annual_bill_savings_net + annual_export_value_net) * ((1.0 - degradation) ** year)
        - annual_opex_net
        for year in range(config.horizon_years)
    ]

    npv_net = npv(capex_net, cashflows, config.discount_rate_percent)
    payback = simple_payback_years(capex_gross, annual_benefit_gross)

    production_total = float(production.sum())
    usage_total = float(usage.sum())
    autoconsumption_percent = (
        round(100.0 * autoconsumed / production_total, 1) if production_total > 0 else 0.0
    )
    self_sufficiency_percent = (
        round(100.0 * autoconsumed / usage_total, 1) if usage_total > 0 else 0.0
    )

    hourly_profile = (
        pd.DataFrame({
            "hour": frame["timestamp"].dt.hour,
            "usage_kwh": usage,
            "production_kwh": production,
            "autoconsumed_kwh": profile_autoconsumed,
        })
        .groupby("hour", as_index=False)
        .mean()
        .round(3)
        .to_dict("records")
    )

    return {
        "kwp": kwp,
        "period_days": period_days,
        "period_production_kwh": round(production_total, 2),
        "period_usage_kwh": round(usage_total, 2),
        "period_autoconsumed_kwh": round(autoconsumed, 2),
        "period_export_kwh": round(grid_export, 2),
        "period_import_kwh": round(grid_import, 2),
        "autoconsumption_percent": autoconsumption_percent,
        "self_sufficiency_percent": self_sufficiency_percent,
        "annual_production_kwh": round(annual_production, 1),
        "annual_usage_kwh": round(annual_usage, 1),
        "annual_autoconsumed_kwh": round(annual_autoconsumed, 1),
        "annual_export_kwh": round(annual_export, 1),
        "annual_import_kwh": round(annual_import, 1),
        "baseline_total_gross": round(baseline_total_net * VAT_ENERGY, 2),
        "with_pv_total_gross": round(with_pv_total_net * VAT_ENERGY, 2),
        "annual_bill_savings_gross": round(annual_bill_savings_net * VAT_ENERGY, 2),
        "annual_export_value_gross": round(annual_export_value_net * VAT_ENERGY, 2),
        "annual_opex_gross": round(annual_opex_net * VAT_ENERGY, 2),
        "annual_net_benefit_gross": round(annual_benefit_gross, 2),
        "capex_pv_net": round(capex_pv_net, 2),
        "capex_battery_net": round(capex_battery_net, 2),
        "capex_battery_gross": round(capex_battery_net * VAT_PV_INSTALL, 2),
        "capex_net": round(capex_net, 2),
        "capex_gross": round(capex_gross, 2),
        "vat_pv_install_percent": round((VAT_PV_INSTALL - 1.0) * 100, 1),
        "vat_energy_percent": round((VAT_ENERGY - 1.0) * 100, 1),
        "simple_payback_years": payback,
        "npv_net": round(npv_net, 2),
        "npv_gross": round(npv_net * VAT_PV_INSTALL, 2),
        "avoided_import_value_period_net": round(avoided_import_value_net, 2),
        "hourly_profile": hourly_profile,
        "battery": battery_stats,
    }


def load_production_profile(config: PvSiteConfig) -> tuple[pd.DataFrame, str]:
    try:
        profile = fetch_pvgis_hourly_per_kwp(
            latitude=config.latitude,
            longitude=config.longitude,
            tilt_deg=config.tilt_deg,
            azimuth_deg=config.azimuth_deg,
            system_loss_percent=config.system_loss_percent,
        )
        return profile, "pvgis"
    except Exception as error:
        return synthetic_hourly_per_kwp(), f"synthetic_fallback ({error})"


def analyze_pv_feasibility(
    csv_path: Path,
    config: PvSiteConfig | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    config = config or load_pv_config()
    prices = prices or load_prices()
    frame = load_usage(csv_path, start_date, end_date)
    production_profile, production_source = load_production_profile(config)

    scenarios = []
    for kwp in config.kwp_scenarios:
        production = production_for_timestamps(
            frame["timestamp"],
            production_profile,
            kwp=kwp,
            inverter_ac_kw=config.inverter_ac_kw,
        )
        scenarios.append(evaluate_kwp(frame, production, prices, config, kwp))

    ranked = sorted(
        scenarios,
        key=lambda item: (
            item["npv_net"],
            -(item["simple_payback_years"] or 10**9),
        ),
        reverse=True,
    )
    recommended = ranked[0] if ranked else None

    return {
        "success": True,
        "config": config.to_dict(),
        "production_source": production_source,
        "period_start": frame["timestamp"].min().strftime("%Y-%m-%d"),
        "period_end": frame["timestamp"].max().strftime("%Y-%m-%d"),
        "tariff": config.tariff,
        "scenarios": scenarios,
        "recommended_kwp": recommended["kwp"] if recommended else None,
        "recommended": recommended,
    }


def config_from_form(data: dict[str, Any], base: PvSiteConfig | None = None) -> PvSiteConfig:
    base = base or PvSiteConfig()
    merged = base.to_dict()

    def _get(name: str, default: Any = None) -> Any:
        if name in data and data[name] not in (None, ""):
            return data[name]
        return default

    merged["latitude"] = float(_get("latitude", merged["latitude"]))
    merged["longitude"] = float(_get("longitude", merged["longitude"]))
    merged["tilt_deg"] = float(_get("tilt_deg", merged["tilt_deg"]))
    merged["azimuth_deg"] = float(_get("azimuth_deg", merged["azimuth_deg"]))
    merged["system_loss_percent"] = float(_get("system_loss_percent", merged["system_loss_percent"]))
    merged["export_price_net_per_kwh"] = float(
        _get("export_price_net_per_kwh", merged["export_price_net_per_kwh"])
    )
    merged["capex_net_per_kwp"] = float(_get("capex_net_per_kwp", merged["capex_net_per_kwp"]))
    merged["battery_capex_net"] = float(
        _get(
            "battery_capex_net",
            merged.get("battery_capex_net", (merged.get("battery") or {}).get("capex_net", 15000.0)),
        )
    )
    merged["opex_net_per_year"] = float(_get("opex_net_per_year", merged["opex_net_per_year"]))
    merged["degradation_percent_per_year"] = float(
        _get("degradation_percent_per_year", merged["degradation_percent_per_year"])
    )
    merged["horizon_years"] = int(_get("horizon_years", merged["horizon_years"]))
    merged["discount_rate_percent"] = float(
        _get("discount_rate_percent", merged["discount_rate_percent"])
    )
    merged["tariff"] = str(_get("tariff", merged["tariff"]))

    kwp_raw = _get("kwp_scenarios", None)
    if kwp_raw is not None:
        if isinstance(kwp_raw, str):
            merged["kwp_scenarios"] = [
                float(part.strip()) for part in kwp_raw.split(",") if part.strip()
            ]
        elif isinstance(kwp_raw, list):
            merged["kwp_scenarios"] = [float(value) for value in kwp_raw]

    inverter = _get("inverter_ac_kw", merged["inverter_ac_kw"])
    merged["inverter_ac_kw"] = None if inverter in (None, "", "null") else float(inverter)

    battery = merged.get("battery") or {}
    battery_enabled = _get("battery_enabled", battery.get("enabled", False))
    if isinstance(battery_enabled, str):
        battery_enabled = battery_enabled.lower() in {"1", "true", "yes", "on"}
    battery["enabled"] = bool(battery_enabled)
    battery["capacity_kwh"] = float(_get("battery_capacity_kwh", battery.get("capacity_kwh", 10.0)))
    battery["charge_power_kw"] = float(
        _get("battery_charge_power_kw", battery.get("charge_power_kw", 5.0))
    )
    battery["discharge_power_kw"] = float(
        _get("battery_discharge_power_kw", battery.get("discharge_power_kw", 5.0))
    )
    battery["round_trip_efficiency"] = float(
        _get("battery_round_trip_efficiency", battery.get("round_trip_efficiency", 0.9))
    )
    battery["degradation_cost_net_per_kwh"] = float(
        _get(
            "battery_degradation_cost_net_per_kwh",
            battery.get("degradation_cost_net_per_kwh", 0.0),
        )
    )
    battery["capex_net"] = float(
        _get("battery_capex_net", battery.get("capex_net", merged.get("battery_capex_net", 15000.0)))
    )
    merged["battery"] = battery
    return PvSiteConfig.from_dict(merged)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", default="uploads/emporia_history.csv")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_FILE))
    parser.add_argument("--kwp", help="Lista mocy kWp, np. 5,8,10")
    parser.add_argument("--tariff", help="Taryfa importu, np. G11")
    parser.add_argument("--battery", action="store_true", help="Włącz magazyn ładowany z PV")
    args = parser.parse_args()

    config = load_pv_config(Path(args.config))
    overrides: dict[str, Any] = {}
    if args.kwp:
        overrides["kwp_scenarios"] = args.kwp
    if args.tariff:
        overrides["tariff"] = args.tariff
    if args.battery:
        overrides["battery_enabled"] = True
    if overrides:
        config = config_from_form(overrides, config)

    result = analyze_pv_feasibility(
        Path(args.csv),
        config=config,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
