"""Symulacja magazynu energii dla taryfy G13 na podstawie CSV Emporia."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import holidays
import pandas as pd


VAT = 1.23
DEFAULT_PRICES_FILE = Path(__file__).with_name("tariff_prices.json")


@dataclass
class BatteryConfig:
    capacity_kwh: float = 10.0
    charge_power_kw: float = 5.0
    discharge_power_kw: float = 5.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    initial_soc_kwh: float = 0.0
    degradation_cost_net_per_kwh: float = 0.0

    @property
    def round_trip_efficiency(self) -> float:
        return self.charge_efficiency * self.discharge_efficiency


def polish_holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("PL", years=year)


def is_holiday(timestamp: pd.Timestamp) -> bool:
    return timestamp.date() in polish_holidays(timestamp.year)


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


def load_prices(path: Path = DEFAULT_PRICES_FILE) -> dict[str, float]:
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    return {key: float(value) for key, value in data["prices"].items()}


def load_usage(path: Path, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(
        frame["Time Bucket (Europe/Budapest)"],
        format="%m/%d/%Y %H:%M:%S",
    )
    phase_columns = [
        "DomPik-Mains_A (kWhs)",
        "DomPik-Mains_B (kWhs)",
        "DomPik-Mains_C (kWhs)",
    ]
    for column in phase_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["usage_kwh"] = frame[phase_columns].sum(axis=1)
    if start_date:
        frame = frame[frame["timestamp"] >= pd.to_datetime(start_date)]
    if end_date:
        frame = frame[frame["timestamp"] < pd.to_datetime(end_date) + pd.Timedelta(days=1)]
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        raise ValueError("Brak danych w wybranym zakresie.")
    return frame


def variable_prices(frame: pd.DataFrame, prices: dict[str, float]) -> pd.Series:
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
    additional = (
        prices["stawka_jakosciowa"]
        + prices["oplata_oze"]
        + prices["oplata_kogeneracyjna"]
    )
    return energy + network + additional


def simulate_g13_battery(
    frame: pd.DataFrame,
    prices: dict[str, float],
    config: BatteryConfig,
) -> dict[str, float]:
    if not 0 < config.charge_efficiency <= 1 or not 0 < config.discharge_efficiency <= 1:
        raise ValueError("Sprawność ładowania i rozładowania musi być w zakresie (0, 1].")
    if config.capacity_kwh <= 0 or config.charge_power_kw <= 0 or config.discharge_power_kw <= 0:
        raise ValueError("Pojemność i moce magazynu muszą być dodatnie.")

    frame = frame.copy()
    frame["variable_price_net"] = variable_prices(frame, prices)
    prices_by_index = frame["variable_price_net"].tolist()
    loads = frame["usage_kwh"].tolist()
    peak_indices = {
        index for index, timestamp in enumerate(frame["timestamp"])
        if g13_zone(timestamp) == "po"
    }
    soc = min(max(config.initial_soc_kwh, 0.0), config.capacity_kwh)
    baseline_net = sum(load * price for load, price in zip(loads, prices_by_index))
    battery_net = 0.0
    discharged_kwh = 0.0
    charged_input_kwh = 0.0

    for index, (load, price) in enumerate(zip(loads, prices_by_index)):
        grid_import = load
        if index in peak_indices and load > 0 and soc > 0:
            delivered = min(load, config.discharge_power_kw, soc * config.discharge_efficiency)
            soc -= delivered / config.discharge_efficiency
            grid_import -= delivered
            discharged_kwh += delivered
            battery_net += delivered * config.degradation_cost_net_per_kwh
        elif index not in peak_indices and soc < config.capacity_kwh:
            future_peak_prices = [
                prices_by_index[future_index]
                for future_index in peak_indices
                if future_index > index
            ]
            if future_peak_prices and price * config.round_trip_efficiency < max(future_peak_prices):
                charge_input = min(
                    config.charge_power_kw,
                    (config.capacity_kwh - soc) / config.charge_efficiency,
                )
                soc += charge_input * config.charge_efficiency
                grid_import += charge_input
                charged_input_kwh += charge_input

        battery_net += grid_import * price

    cycles = discharged_kwh / config.capacity_kwh
    fixed_net = prices["skladnik_staly_ss"] + prices["oplata_abonamentowa"] + prices["oplata_mocowa"]
    months = max(1, round(((frame["timestamp"].max() - frame["timestamp"].min()).days + 1) / 30.44))
    baseline_total = baseline_net + fixed_net * months
    battery_total = battery_net + fixed_net * months
    return {
        "baseline_variable_net": round(baseline_net, 2),
        "battery_variable_net": round(battery_net, 2),
        "baseline_total_gross": round(baseline_total * VAT, 2),
        "battery_total_gross": round(battery_total * VAT, 2),
        "savings_gross": round((baseline_total - battery_total) * VAT, 2),
        "charged_input_kwh": round(charged_input_kwh, 2),
        "discharged_to_load_kwh": round(discharged_kwh, 2),
        "cycles_equivalent": round(cycles, 2),
        "final_soc_kwh": round(soc, 2),
        "round_trip_efficiency_percent": round(config.round_trip_efficiency * 100, 1),
        "data_usage_kwh": round(sum(loads), 2),
    }


def simulate_pv_battery(
    usage_kwh,
    production_kwh,
    import_price_net,
    export_price_net: float,
    config: BatteryConfig,
) -> dict[str, float]:
    """Ładuje magazyn z nadwyżki PV i rozładowuje na niedobór load (bez arbitrażu taryfowego)."""
    if not 0 < config.charge_efficiency <= 1 or not 0 < config.discharge_efficiency <= 1:
        raise ValueError("Sprawność ładowania i rozładowania musi być w zakresie (0, 1].")
    if config.capacity_kwh <= 0 or config.charge_power_kw <= 0 or config.discharge_power_kw <= 0:
        raise ValueError("Pojemność i moce magazynu muszą być dodatnie.")

    loads = list(usage_kwh)
    production = list(production_kwh)
    prices = list(import_price_net)
    if not (len(loads) == len(production) == len(prices)):
        raise ValueError("Zużycie, produkcja i ceny muszą mieć tę samą długość.")

    soc = min(max(config.initial_soc_kwh, 0.0), config.capacity_kwh)
    autoconsumed_direct = 0.0
    charged_from_pv = 0.0
    discharged_to_load = 0.0
    grid_import = 0.0
    grid_export = 0.0
    grid_import_cost_net = 0.0
    export_value_net = 0.0
    avoided_import_value_net = 0.0
    degradation_cost_net = 0.0

    for load, produced, price in zip(loads, production, prices):
        direct = min(load, produced)
        remaining_load = load - direct
        remaining_pv = produced - direct
        autoconsumed_direct += direct
        avoided_import_value_net += direct * price

        if remaining_pv > 0 and soc < config.capacity_kwh:
            charge_input = min(
                remaining_pv,
                config.charge_power_kw,
                (config.capacity_kwh - soc) / config.charge_efficiency,
            )
            soc += charge_input * config.charge_efficiency
            remaining_pv -= charge_input
            charged_from_pv += charge_input

        if remaining_load > 0 and soc > 0:
            delivered = min(
                remaining_load,
                config.discharge_power_kw,
                soc * config.discharge_efficiency,
            )
            soc -= delivered / config.discharge_efficiency
            remaining_load -= delivered
            discharged_to_load += delivered
            avoided_import_value_net += delivered * price
            degradation_cost_net += delivered * config.degradation_cost_net_per_kwh

        grid_import += remaining_load
        grid_import_cost_net += remaining_load * price
        grid_export += remaining_pv
        export_value_net += remaining_pv * export_price_net

    cycles = discharged_to_load / config.capacity_kwh if config.capacity_kwh else 0.0
    return {
        "autoconsumed_direct_kwh": autoconsumed_direct,
        "charged_from_pv_kwh": charged_from_pv,
        "discharged_to_load_kwh": discharged_to_load,
        "grid_import_kwh": grid_import,
        "grid_export_kwh": grid_export,
        "grid_import_cost_net": grid_import_cost_net,
        "export_value_net": export_value_net,
        "avoided_import_value_net": avoided_import_value_net,
        "degradation_cost_net": degradation_cost_net,
        "cycles_equivalent": cycles,
        "final_soc_kwh": soc,
        "round_trip_efficiency_percent": config.round_trip_efficiency * 100.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", default="uploads/emporia_history.csv")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--capacity", type=float, default=10.0, help="Pojemność kWh")
    parser.add_argument("--charge-power", type=float, default=5.0, help="Moc ładowania kW")
    parser.add_argument("--discharge-power", type=float, default=5.0, help="Moc rozładowania kW")
    parser.add_argument("--efficiency", type=float, default=0.90, help="Sprawność całego cyklu")
    parser.add_argument("--degradation-cost", type=float, default=0.0, help="Koszt degradacji netto zł/kWh oddanej energii")
    parser.add_argument(
        "--pv-production-csv",
        help="Opcjonalny CSV z kolumną production_kwh (ta sama długość co zużycie) — tryb ładowania z PV",
    )
    parser.add_argument("--export-price", type=float, default=0.25, help="Cena eksportu netto zł/kWh (tryb PV)")
    args = parser.parse_args()
    efficiency = args.efficiency ** 0.5
    config = BatteryConfig(
        capacity_kwh=args.capacity,
        charge_power_kw=args.charge_power,
        discharge_power_kw=args.discharge_power,
        charge_efficiency=efficiency,
        discharge_efficiency=efficiency,
        degradation_cost_net_per_kwh=args.degradation_cost,
    )
    frame = load_usage(Path(args.csv), args.start_date, args.end_date)
    if args.pv_production_csv:
        production_frame = pd.read_csv(args.pv_production_csv)
        if "production_kwh" not in production_frame.columns:
            raise SystemExit("Plik produkcji musi zawierać kolumnę production_kwh")
        prices = load_prices()
        import_prices = variable_prices(frame, prices)
        result = simulate_pv_battery(
            usage_kwh=frame["usage_kwh"].to_numpy(),
            production_kwh=production_frame["production_kwh"].to_numpy(),
            import_price_net=import_prices.to_numpy(),
            export_price_net=args.export_price,
            config=config,
        )
    else:
        result = simulate_g13_battery(frame, load_prices(), config)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
