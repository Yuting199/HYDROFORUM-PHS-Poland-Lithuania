#!/usr/bin/env python3
"""Descriptive annual price levels, daily spreads and fixed-2021 tail diagnostics."""

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from statistics import fmean, median

import numpy as np
import pyarrow.parquet as pq


STUDY = Path(__file__).resolve().parents[1]
INPUT = STUDY / "data/prices_PL_LT_hourly_2021_2025.parquet"
OUTPUT = STUDY / "data/price_spread_diagnostics_2021_2025.csv"
YEARS = range(2021, 2026)
COMPARISONS = ((2021, 2022), (2022, 2023), (2023, 2025), (2021, 2025))
# Previously reported two-decimal daily spreads, retained as reconciliation targets.
REPORTED_SPREADS = {
    "PL": (51.68, 145.34, 71.74, 121.54, 144.32),
    "LT": (92.17, 245.41, 122.59, 151.78, 164.62),
}


def conditional_summary(prices, selected):
    values = prices[selected]
    return (len(values), 100.0 * len(values) / len(prices) if len(prices) else math.nan,
            fmean(values) if len(values) else math.nan)


def daily_metrics(prices):
    """A complete 24-hour UTC day; order statistics do not represent dispatch."""
    ordered = sorted(prices)
    low, high = ordered[0], ordered[-1]
    low_six, high_six = fmean(ordered[:6]), fmean(ordered[-6:])
    return low, high, fmean(prices), high - low, low_six, high_six, high_six - low_six


def analyse():
    table = pq.read_table(INPUT, columns=["timestamp_utc", "PL_EUR_MWh", "LT_EUR_MWh"])
    times = table.column("timestamp_utc").to_pylist()
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    if table.schema.field("timestamp_utc").type.tz != "UTC" or len(times) != 43824:
        raise ValueError("Expected the complete 2021-2025 UTC hourly index")
    if any(t != start + timedelta(hours=i) for i, t in enumerate(times)):
        raise ValueError("Missing, duplicate or misordered hourly timestamps")
    rows = []
    for country in ("PL", "LT"):
        all_prices = np.asarray(table.column(f"{country}_EUR_MWh").to_pylist(), dtype=float)
        reference = all_prices[:8760]
        if not np.isfinite(reference).all():
            raise ValueError(f"Incomplete 2021 reference prices for {country}")
        p10, p90 = np.quantile(reference, [0.1, 0.9], method="linear")
        offset = 0
        for year in YEARS:
            expected_hours = 8784 if year == 2024 else 8760
            annual = all_prices[offset:offset + expected_hours]
            offset += expected_hours
            valid = annual[np.isfinite(annual)]
            complete = [daily_metrics(day) for day in annual.reshape(-1, 24) if np.isfinite(day).all()]
            if not complete:
                raise ValueError(f"No complete UTC days: {country} {year}")
            low, high, daily_mean, spread, low_six, high_six, band = map(list, zip(*complete))
            row = {
                "country": country, "year": year, "expected_hours": expected_hours,
                "valid_hours": len(valid), "missing_hours": expected_hours - len(valid),
                "complete_utc_days": len(complete),
                "excluded_utc_days": expected_hours // 24 - len(complete),
                "mean_daily_min_eur_mwh": fmean(low),
                "mean_daily_max_eur_mwh": fmean(high),
                "mean_daily_mean_eur_mwh": fmean(daily_mean),
                "mean_daily_spread_eur_mwh": fmean(spread),
                "median_daily_spread_eur_mwh": median(spread),
                "mean_daily_low_six_eur_mwh": fmean(low_six),
                "mean_daily_high_six_eur_mwh": fmean(high_six),
                "mean_daily_six_hour_band_spread_eur_mwh": fmean(band),
                "fixed_2021_p10_eur_mwh": float(p10),
                "fixed_2021_p90_eur_mwh": float(p90),
            }
            for label, selected in (("below_p10", valid < p10), ("above_p90", valid > p90),
                                    ("negative_price", valid < 0)):
                count, percentage, mean_price = conditional_summary(valid, selected)
                row[f"{label}_hours"] = count
                row[f"{label}_pct"] = percentage
                row[f"{label}_mean_eur_mwh"] = mean_price
            for lo, hi, sp in (("min", "max", "spread"),
                               ("low_six", "high_six", "six_hour_band_spread")):
                error = row[f"mean_daily_{sp}_eur_mwh"] - (
                    row[f"mean_daily_{hi}_eur_mwh"] - row[f"mean_daily_{lo}_eur_mwh"])
                if abs(error) > 1e-10:
                    raise ValueError(f"Spread identity failed: {country} {year} {sp}")
            reported = REPORTED_SPREADS[country][year - 2021]
            if round(row["mean_daily_spread_eur_mwh"], 2) != reported:
                raise ValueError(f"Previously reported spread changed: {country} {year}")
            rows.append(row)
    return rows


def contributions(rows):
    lookup = {(r["country"], r["year"]): r for r in rows}
    results = []
    for country in ("PL", "LT"):
        for a, b in COMPARISONS:
            before, after = lookup[(country, a)], lookup[(country, b)]
            result = {"country": country, "comparison": f"{a}-{b}"}
            for prefix, lo, hi, spread in (
                ("endpoint", "min", "max", "spread"),
                ("six_hour", "low_six", "high_six", "six_hour_band_spread"),
            ):
                high = after[f"mean_daily_{hi}_eur_mwh"] - before[f"mean_daily_{hi}_eur_mwh"]
                low = before[f"mean_daily_{lo}_eur_mwh"] - after[f"mean_daily_{lo}_eur_mwh"]
                change = after[f"mean_daily_{spread}_eur_mwh"] - before[f"mean_daily_{spread}_eur_mwh"]
                if abs(high + low - change) > 1e-10:
                    raise ValueError(f"Contribution identity failed: {country} {a}-{b}")
                result.update({f"{prefix}_high": high, f"{prefix}_low": low, f"{prefix}_change": change})
            results.append(result)
    return results


def print_table(rows, columns):
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(f"{row[k]:.4f}" if isinstance(row[k], float) else str(row[k]) for k in columns))


def main():
    empty = conditional_summary(np.array([1.0, 2.0]), np.array([False, False]))
    if empty[:2] != (0, 0.0) or not math.isnan(empty[2]):
        raise ValueError("Empty-tail conditional mean must be NaN")
    if daily_metrics(list(range(24))) != (0, 23, 11.5, 23, 2.5, 20.5, 18.0):
        raise ValueError("Daily order-statistic check failed")
    input_hash = hashlib.sha256(INPUT.read_bytes()).hexdigest()
    rows = analyse()
    changes = contributions(rows)
    if hashlib.sha256(INPUT.read_bytes()).hexdigest() != input_hash:
        raise ValueError("Input changed during the analysis")
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print_table(rows, ["country", "year", "mean_daily_min_eur_mwh", "mean_daily_max_eur_mwh",
                      "mean_daily_mean_eur_mwh", "mean_daily_spread_eur_mwh",
                      "median_daily_spread_eur_mwh", "mean_daily_low_six_eur_mwh",
                      "mean_daily_high_six_eur_mwh", "mean_daily_six_hour_band_spread_eur_mwh"])
    print("\nSigned contributions in EUR/MWh:")
    print_table(changes, list(changes[0]))
    print("\nFixed thresholds and conditional frequency/severity:")
    print_table(rows, ["country", "year", "fixed_2021_p10_eur_mwh", "fixed_2021_p90_eur_mwh",
                      "below_p10_hours", "below_p10_pct", "below_p10_mean_eur_mwh",
                      "above_p90_hours", "above_p90_pct", "above_p90_mean_eur_mwh",
                      "negative_price_hours", "negative_price_mean_eur_mwh"])
    print(f"\nPASS: all identities and previous spreads reconciled. Wrote {len(rows)} rows to {OUTPUT}")
    print(f"Input SHA-256: {input_hash}; NumPy {np.__version__}; quantile method: linear")


if __name__ == "__main__":
    main()
