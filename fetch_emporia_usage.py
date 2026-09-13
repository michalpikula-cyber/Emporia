"""Pobiera godzinowe zuzycie z kanalu glownego Emporia do CSV aplikacji."""

import argparse
import csv
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def get_required_setting(name):
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Brak ustawienia {name} w pliku .env.")
    return value


def login():
    vue = PyEmVue()
    token_file = BASE_DIR / os.getenv(
        "EMPORIA_TOKEN_STORAGE_FILE", ".emporia_tokens.json"
    )
    if not vue.login(
        username=get_required_setting("EMPORIA_USERNAME"),
        password=get_required_setting("EMPORIA_PASSWORD"),
        token_storage_file=str(token_file),
    ):
        raise SystemExit("Logowanie do Emporia nie powiodlo sie.")
    return vue


def find_main_channel(devices):
    for device in devices:
        for channel in device.channels:
            if channel.channel_num == "1,2,3":
                return channel
    raise SystemExit("Nie znaleziono kanalu glownego 1,2,3.")


def fetch_usage(vue, days):
    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=days)
    return fetch_usage_range(vue, start, end)


def fetch_usage_range(vue, start, end):
    channel = find_main_channel(vue.get_devices())
    hourly_usage = {}
    chunk_end = end

    # API odrzuca duze zapytania, dlatego pobieramy historie partiami.
    while chunk_end > start:
        chunk_start = max(start, chunk_end - dt.timedelta(days=30))
        usage, first_instant = vue.get_chart_usage(
            channel,
            start=chunk_start,
            end=chunk_end,
            scale=Scale.HOUR.value,
            unit=Unit.KWH.value,
        )
        for index, value in enumerate(usage):
            timestamp = first_instant + dt.timedelta(hours=index)
            if start <= timestamp < end:
                hourly_usage[timestamp] = float(value) if value is not None else 0.0
        chunk_end = chunk_start

    ordered_usage = sorted(hourly_usage.items())
    return ordered_usage


def sync_history(vue, path, initial_days=365):
    existing = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                timestamp = dt.datetime.strptime(
                    row["Time Bucket (Europe/Budapest)"], "%m/%d/%Y %H:%M:%S"
                ).replace(tzinfo=ZoneInfo("Europe/Budapest")).astimezone(dt.timezone.utc)
                existing.append((timestamp, float(row["DomPik-Mains_A (kWhs)"])))

    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    target_start = end - dt.timedelta(days=initial_days)
    new_usage = []
    if existing:
        earliest = min(timestamp for timestamp, _ in existing)
        latest = max(timestamp for timestamp, _ in existing)
        if target_start < earliest:
            new_usage.extend(fetch_usage_range(vue, target_start, earliest))
        if latest + dt.timedelta(hours=1) < end:
            new_usage.extend(
                fetch_usage_range(vue, latest + dt.timedelta(hours=1), end)
            )
    else:
        new_usage = fetch_usage_range(vue, target_start, end)
    existing_timestamps = {timestamp for timestamp, _ in existing}
    existing.extend(
        (timestamp, value)
        for timestamp, value in new_usage
        if timestamp not in existing_timestamps
    )

    if new_usage or not path.exists():
        write_csv(path, sorted(existing))
    return len(new_usage), len(existing)


def write_csv(path, usage):
    timezone = ZoneInfo("Europe/Budapest")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Time Bucket (Europe/Budapest)",
        "DomPik-Mains_A (kWhs)",
        "DomPik-Mains_B (kWhs)",
        "DomPik-Mains_C (kWhs)",
    ]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for timestamp, value in usage:
            timestamp = timestamp.astimezone(timezone)
            writer.writerow(
                {
                    fieldnames[0]: timestamp.strftime("%m/%d/%Y %H:%M:%S"),
                    fieldnames[1]: round(float(value), 6),
                    fieldnames[2]: 0,
                    fieldnames[3]: 0,
                }
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--output",
        default="uploads/emporia_api_test.csv",
        help="Sciezka wyjsciowego pliku CSV",
    )
    args = parser.parse_args()
    if args.days < 1:
        raise SystemExit("Liczba dni musi byc dodatnia.")

    vue = login()
    usage = fetch_usage(vue, args.days)
    if not usage:
        raise SystemExit("Emporia nie zwrocila danych dla wybranego zakresu.")

    output_path = BASE_DIR / args.output
    write_csv(output_path, usage)
    print(f"Pobrano {len(usage)} godzin do {output_path}")
    print("Dane sa suma kanalu glownego 1,2,3; fazy B i C zapisano jako 0.")


if __name__ == "__main__":
    main()
