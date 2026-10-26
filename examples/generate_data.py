"""Regenerate the public synthetic fixture without network or randomness."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open", "high", "low", "close", "base_volume", "synthetic"])
        previous = 100
        for index in range(96):
            phase = index % 24
            close = 100 + (phase if phase < 12 else 24-phase) + (index//24)*2
            opened = previous + (1 if index % 7 == 0 else 0)
            when = datetime(2024, 1, 1, tzinfo=timezone.utc)+timedelta(hours=index)
            writer.writerow([when.isoformat(), opened, max(opened,close)+1, min(opened,close)-1, close, 1000, "true"])
            previous = close


if __name__ == "__main__":
    generate(Path(__file__).with_name("synthetic_hourly.csv"))
