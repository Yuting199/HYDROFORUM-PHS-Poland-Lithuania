#!/usr/bin/env python3
"""Prepare the HYDROFORUM PL/LT inspection prices from existing ENTSO-E files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean


STUDY = Path(__file__).resolve().parents[1]
REPO = STUDY.parent
DATASET = "EnergyPrices_12.1.D_r3"
RAW_DIR = REPO / "project_root/data/raw/entsoe" / DATASET
MANIFEST = REPO / "project_root/data/interim/manifests/entsoe_energy_prices_download_manifest.csv"
START = datetime(2021, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)
ZONES = {"PL": "10YPL-AREA-----S", "LT": "10YLT-1001A0008Q"}
RESOLUTIONS = {"PT60M": 60, "PT15M": 15}
RAW_COLUMNS = (
    "InstanceCode", "DateTime(UTC)", "ResolutionCode", "AreaCode",
    "AreaDisplayName", "AreaTypeCode", "MapCode", "ContractType", "Sequence",
    "Price[Currency/MWh]", "Currency", "UpdateTime(UTC)",
)
SCHEMA_URL = "https://transparencyplatform.zendesk.com/hc/en-us/articles/29036341566737-EnergyPrices-12-1-D-r3"
TIME_URL = "https://eepublicdownloads.entsoe.eu/clean-documents/EDI/Library/cim_based/Introduction_of_different_Timeseries_possibilities__curvetypes__with_ENTSO-E_electronic_document_v1.4.pdf"


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def parse_price(value: str) -> float | None:
    try:
        price = float(value)
    except ValueError:
        return None
    return price if math.isfinite(price) else None


def read_manifest() -> dict:
    downloads = defaultdict(list)
    with MANIFEST.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["dataset"] == DATASET and row["status"] == "downloaded":
                downloads[row["original_filename"]].append(row)
    return downloads


def read_native() -> tuple[list[dict], dict]:
    downloads = read_manifest()
    records = {}
    audit = {"source_files": [], "exact_duplicates_removed": 0, "excluded_rows": Counter()}
    for year in range(2021, 2026):
        for month in range(1, 13):
            path = RAW_DIR / f"{year}_{month:02d}_{DATASET}.csv"
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            file_meta = {
                "filename": path.name, "sha256": digest, "size_bytes": path.stat().st_size,
                "download_records": downloads.get(path.name, []),
            }
            audit["source_files"].append(file_meta)
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                if tuple(reader.fieldnames or ()) != RAW_COLUMNS:
                    raise ValueError(f"Unexpected source schema: {path}")
                for line, raw in enumerate(reader, 2):
                    zone = raw["MapCode"]
                    if zone not in ZONES:
                        continue
                    if raw["AreaCode"] != ZONES[zone] or raw["AreaTypeCode"] != "BZN":
                        audit["excluded_rows"]["other_area"] += 1
                        continue
                    if raw["ContractType"] != "Day-ahead":
                        audit["excluded_rows"]["other_contract"] += 1
                        continue
                    if raw["Currency"] != "EUR":
                        raise ValueError(f"Unexpected currency at {path.name}:{line}")
                    timestamp = parse_utc(raw["DateTime(UTC)"])
                    if not START <= timestamp < END:
                        audit["excluded_rows"]["outside_window"] += 1
                        continue
                    resolution = raw["ResolutionCode"]
                    if resolution not in RESOLUTIONS:
                        raise ValueError(f"Unsupported resolution at {path.name}:{line}: {resolution}")
                    minutes = RESOLUTIONS[resolution]
                    if timestamp.second or timestamp.minute % minutes:
                        raise ValueError(f"Misaligned interval at {path.name}:{line}")
                    key = tuple(raw[column] for column in RAW_COLUMNS)
                    location = {"filename": path.name, "row_number": line}
                    if key in records:
                        records[key]["source_records"].append(location)
                        audit["exact_duplicates_removed"] += 1
                        continue
                    price = parse_price(raw["Price[Currency/MWh]"])
                    issues = [] if price is not None else ["missing_or_invalid_price"]
                    if raw["Sequence"].strip():
                        issues.append("unconfirmed_sequence")
                    records[key] = {
                        "timestamp_utc": timestamp,
                        "interval_end_utc": timestamp + timedelta(minutes=minutes),
                        "duration_minutes": minutes,
                        "zone": zone,
                        "area_code": raw["AreaCode"],
                        "area_display_name": raw["AreaDisplayName"],
                        "area_type_code": raw["AreaTypeCode"],
                        "contract_type": raw["ContractType"],
                        "resolution_code": resolution,
                        "price_eur_mwh": price,
                        "currency": raw["Currency"],
                        "source_filename": path.name,
                        "source_records": [location],
                        "source_datetime_text": raw["DateTime(UTC)"],
                        "source_price_text": raw["Price[Currency/MWh]"],
                        "instance_code": raw["InstanceCode"],
                        "sequence_raw": raw["Sequence"],
                        "update_time_utc": parse_utc(raw["UpdateTime(UTC)"]) if raw["UpdateTime(UTC)"].strip() else None,
                        "issues": issues,
                    }
    native = sorted(records.values(), key=lambda r: (r["timestamp_utc"], r["zone"], r["resolution_code"], r["instance_code"]))
    if any(row["interval_end_utc"] > END for row in native):
        raise ValueError("An interval crosses the requested exclusive boundary")
    return native, audit


def hour_price(rows: list[dict]) -> tuple[float | None, str]:
    if not rows:
        return None, "missing_hour"
    resolutions = {row["resolution_code"] for row in rows}
    if len(resolutions) != 1:
        return None, "overlapping_resolutions"
    timestamps = [row["timestamp_utc"] for row in rows]
    if len(set(timestamps)) != len(timestamps):
        return None, "unresolved_records"
    if any(row["issues"] for row in rows):
        return None, "invalid_or_unconfirmed_record"
    if resolutions == {"PT60M"} and len(rows) == 1 and timestamps[0].minute == 0:
        return rows[0]["price_eur_mwh"], "native_hourly"
    if resolutions == {"PT15M"} and len(rows) == 4 and {t.minute for t in timestamps} == {0, 15, 30, 45}:
        return fmean(row["price_eur_mwh"] for row in rows), "four_quarter_hours"
    return None, "incomplete_hour"


def make_hourly(native: list[dict], start: datetime = START, end: datetime = END) -> tuple[list[dict], dict]:
    groups = defaultdict(list)
    for row in native:
        hour = row["timestamp_utc"].replace(minute=0, second=0, microsecond=0)
        groups[(hour, row["zone"])].append(row)
    hourly = []
    quality = {zone: Counter() for zone in ZONES}
    hour = start
    while hour < end:
        output = {"timestamp_utc": hour}
        for zone in ZONES:
            rows = groups.get((hour, zone), [])
            value, status = hour_price(rows)
            output[f"{zone}_EUR_MWh"] = value
            quality[zone][status] += 1
            if status not in {"native_hourly", "four_quarter_hours"}:
                for row in rows:
                    if status not in row["issues"]:
                        row["issues"].append(status)
        hourly.append(output)
        hour += timedelta(hours=1)
    return hourly, quality


def summary_table(hourly: list[dict]) -> str:
    lines = [
        "| Country | Year | Valid hours | Missing hours | Mean EUR/MWh | Mean daily spread EUR/MWh | Negative hours | Complete UTC days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for zone in ZONES:
        for year in range(2021, 2026):
            rows = [r for r in hourly if r["timestamp_utc"].year == year]
            expected = 8784 if year == 2024 else 8760
            if len(rows) != expected or len({r["timestamp_utc"] for r in rows}) != expected:
                raise ValueError(f"Wrong hourly index for {zone} {year}")
            values = [r[f"{zone}_EUR_MWh"] for r in rows if r[f"{zone}_EUR_MWh"] is not None]
            days = defaultdict(list)
            for row in rows:
                days[row["timestamp_utc"].date()].append(row[f"{zone}_EUR_MWh"])
            spreads = [max(v) - min(v) for v in days.values() if len(v) == 24 and all(p is not None for p in v)]
            mean = f"{fmean(values):.2f}" if values else "NA"
            spread = f"{fmean(spreads):.2f}" if spreads else "NA"
            lines.append(f"| {zone} | {year} | {len(values)} | {expected - len(values)} | {mean} | {spread} | {sum(v < 0 for v in values)} | {len(spreads)} |")
    return "\n".join(lines)


def write_outputs(native: list[dict], hourly: list[dict], audit: dict, table: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    utc = pa.timestamp("us", tz="UTC")
    native_schema = pa.schema([
        ("timestamp_utc", utc), ("interval_end_utc", utc), ("duration_minutes", pa.int16()),
        ("zone", pa.string()), ("area_code", pa.string()), ("area_display_name", pa.string()),
        ("area_type_code", pa.string()), ("contract_type", pa.string()),
        ("resolution_code", pa.string()), ("price_eur_mwh", pa.float64()), ("currency", pa.string()),
        ("source_filename", pa.string()),
        ("source_records", pa.list_(pa.struct([("filename", pa.string()), ("row_number", pa.int64())]))),
        ("source_datetime_text", pa.string()), ("source_price_text", pa.string()),
        ("instance_code", pa.string()), ("sequence_raw", pa.string()),
        ("update_time_utc", utc), ("issues", pa.list_(pa.string())),
    ])
    hourly_schema = pa.schema([
        ("timestamp_utc", utc), ("PL_EUR_MWh", pa.float64()), ("LT_EUR_MWh", pa.float64()),
    ])
    audit.update({
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "pyarrow_version": pa.__version__, "source": "ENTSO-E Transparency Platform FMS",
        "dataset": DATASET, "raw_directory": str(RAW_DIR),
        "source_manifest": str(MANIFEST), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_schema_url": SCHEMA_URL, "interval_convention_url": TIME_URL,
        "interval_convention": "UTC start inclusive, end exclusive; no timestamp shifts",
        "window_start_inclusive": START.isoformat(), "window_end_exclusive": END.isoformat(),
        "hourly_rule": "One native PT60M observation or four distinct complete PT15M observations; otherwise null",
        "duplicate_rule": "Collapse only identical raw records; preserve all source locations. Other competing records remain unresolved.",
    })
    metadata = {b"hydroforum_provenance": json.dumps(audit, sort_keys=True).encode()}
    data = STUDY / "data"
    data.mkdir(exist_ok=True)
    (STUDY / "figures").mkdir(exist_ok=True)
    for name, rows, schema in [("native", native, native_schema), ("hourly", hourly, hourly_schema)]:
        output = data / f"prices_PL_LT_{name}_2021_2025.parquet"
        arrow_table = pa.Table.from_pylist(rows, schema=schema.with_metadata(metadata))
        pq.write_table(arrow_table, output, compression="snappy")
        if not pq.read_table(output).equals(arrow_table):
            raise ValueError(f"Parquet round-trip failed: {output}")
        print(f"Wrote and verified {output}: {len(rows):,} rows")
    readme = STUDY / "README.md"
    before, tail = readme.read_text(encoding="utf-8").split("<!-- coverage:start -->", 1)
    _, after = tail.split("<!-- coverage:end -->", 1)
    coverage = (
        f"\n\nPrepared {audit['prepared_at_utc']} with PyArrow {pa.__version__}. "
        f"Native rows: {len(native):,}; combined hourly rows: {len(hourly):,}. "
        f"Exact duplicates removed: {audit['exact_duplicates_removed']}.\n\n"
        + table + "\n\nHourly construction counts by country:\n\n```json\n"
        + json.dumps(audit["hourly_quality"], indent=2, sort_keys=True)
        + "\n```\n\n<!-- coverage:end -->"
    )
    readme.write_text(before + "<!-- coverage:start -->" + coverage + after, encoding="utf-8")


def self_test() -> None:
    def row(minute=0, resolution="PT60M", price=-10.0):
        return {"timestamp_utc": START + timedelta(minutes=minute), "zone": "PL",
                "resolution_code": resolution, "price_eur_mwh": price,
                "issues": [] if price is not None else ["missing_or_invalid_price"]}
    assert hour_price([row()]) == (-10.0, "native_hourly")
    quarters = [row(m, "PT15M", p) for m, p in zip((0, 15, 30, 45), (-100.0, 0.0, 100.0, 4000.0))]
    assert hour_price(quarters) == (1000.0, "four_quarter_hours")
    assert hour_price(quarters[:3])[0] is None
    assert hour_price([quarters[0], quarters[0], *quarters[2:]])[0] is None
    assert hour_price([row(), *quarters])[0] is None
    assert hour_price([row(), row(price=100)])[0] is None
    assert hour_price([row(price=None)])[0] is None
    assert hour_price([])[0] is None
    assert parse_price("-500") == -500 and parse_price("4000") == 4000
    assert all(parse_price(s) is None for s in ("", " ", "NaN", "inf", "bad"))
    output, _ = make_hourly([row()], START, START + timedelta(hours=2))
    assert len(output) == 2 and output[0]["PL_EUR_MWh"] == -10
    assert output[0]["LT_EUR_MWh"] is None and output[1]["PL_EUR_MWh"] is None
    print("Cleaning edge-case checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Validate sources and print statistics without writing files")
    parser.add_argument("--self-test", action="store_true", help="Run small in-memory cleaning checks without reading raw data")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    native, audit = read_native()
    hourly, quality = make_hourly(native)
    audit["hourly_quality"] = quality
    table = summary_table(hourly)
    print(table)
    print("Hourly quality:", json.dumps(quality, sort_keys=True))
    print("Exact duplicates removed:", audit["exact_duplicates_removed"])
    if not args.check_only:
        write_outputs(native, hourly, audit, table)


if __name__ == "__main__":
    main()
