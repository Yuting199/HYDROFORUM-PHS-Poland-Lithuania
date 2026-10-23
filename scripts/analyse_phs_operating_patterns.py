#!/usr/bin/env python3
"""Describe saved baseline PHS schedules in local civil time; no optimisation."""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import run_phs as phs


STUDY = Path(__file__).resolve().parents[1]
ZONES = {"PL": ZoneInfo("Europe/Warsaw"), "LT": ZoneInfo("Europe/Vilnius")}
NAMES = {"PL": "Poland", "LT": "Lithuania"}
COLORS = {"PL": "#0072B2", "LT": "#009E73"}
SEASONS = ("winter", "spring", "summer", "autumn")
WINDOWS = ("midday", "night", "other")
PATTERNS = ("midday_dominant", "night_dominant", "both", "other", "no_pumping")
PATTERN_LABELS = ("Midday-led", "Night-led", "Both", "Other", "No pumping")
PATTERN_COLORS = ("#E69F00", "#0072B2", "#009E73", "#999999", "#FFFFFF")
ACTIVITY_TOL_MW = phs.PHYSICAL_TOL
DAY_TOL_MWH = 1e-6
SHARE_TOL = 1e-10
BASE_RULE = dict(midday_start=10, night_start=22, both_threshold=0.20, dominant_threshold=0.60)
SCENARIOS = {
    "base": {},
    "midday_early": {"midday_start": 9}, "midday_late": {"midday_start": 11},
    "night_early": {"night_start": 21}, "night_late": {"night_start": 23},
    "windows_early": {"midday_start": 9, "night_start": 21},
    "windows_late": {"midday_start": 11, "night_start": 23},
    "both_15pct": {"both_threshold": 0.15}, "both_25pct": {"both_threshold": 0.25},
    "dominant_50pct": {"dominant_threshold": 0.50},
    "dominant_70pct": {"dominant_threshold": 0.70},
}
OUTPUTS = {
    "hourly": STUDY / "data/phs_operating_hourly_profiles.csv",
    "period": STUDY / "data/phs_operating_period_summary.csv",
    "daily": STUDY / "data/phs_operating_daily.parquet",
    "robustness": STUDY / "data/phs_operating_pattern_robustness.csv",
    "audit": STUDY / "data/phs_operating_patterns_audit.json",
}
FIGURES = ("figure_phs_seasonal_pumping", "figure_phs_daily_patterns")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def season(month):
    return SEASONS[(month % 12) // 3]


def window(hour, rule=BASE_RULE):
    if (hour - rule["midday_start"]) % 24 < 6:
        return "midday"
    if (hour - rule["night_start"]) % 24 < 8:
        return "night"
    return "other"


def classify(energy, rule=BASE_RULE):
    total = math.fsum(energy.values())
    if total <= DAY_TOL_MWH:
        return "no_pumping"
    midday, night = energy["midday"] / total, energy["night"] / total
    # Treat floating-point representations of an exact threshold consistently.
    if min(midday, night) >= rule["both_threshold"] - SHARE_TOL:
        return "both"
    if midday >= rule["dominant_threshold"] - SHARE_TOL:
        return "midday_dominant"
    if night >= rule["dominant_threshold"] - SHARE_TOL:
        return "night_dominant"
    return "other"


def expected_day(day, zone):
    start = datetime.combine(day, time(), zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(timezone.utc)
    return [start + timedelta(hours=i) for i in range(int((end - start).total_seconds() / 3600))]


def self_checks():
    assert [window(h) for h in (0, 5, 6, 9, 10, 15, 16, 21, 22, 23)] == [
        "night", "night", "other", "other", "midday", "midday", "other", "other", "night", "night"]
    for rule_changes in SCENARIOS.values():
        rule = BASE_RULE | rule_changes
        assert Counter(window(h, rule) for h in range(24)) == {"midday": 6, "night": 8, "other": 10}
    for energy, expected in [
        ((0, 0, 0), "no_pumping"), ((0.6, 0.1, 0.3), "midday_dominant"),
        ((0.1, 0.6, 0.3), "night_dominant"), ((0.2, 0.2, 0.6), "both"),
        ((0.1, 0.1, 0.8), "other"), ((1e-9, 0, 0), "no_pumping"),
    ]:
        assert classify(dict(zip(WINDOWS, energy))) == expected
    assert classify(dict(midday=5, night=1.9999999999999998, other=3)) == "both"
    assert classify(dict(midday=5, night=1.99999, other=3.00001)) == "other"
    for zone in ZONES.values():
        for year, spring, autumn in ((2023, 26, 29), (2024, 31, 27), (2025, 30, 26)):
            assert len(expected_day(date(year, 3, spring), zone)) == 23
            repeated = expected_day(date(year, 10, autumn), zone)
            assert len(repeated) == 25
            assert sum(t.astimezone(zone).fold for t in repeated) == 1
    print("PASS classification, windows, and all 12 country-year DST transitions", flush=True)


def load_validated():
    p = phs.Parameters(pumping_formulation="continuous")
    annual_prices = phs.read_annual_prices(parameters=p)
    summaries, reference_audit = phs.load_reference(annual_prices, p, digest(phs.INPUT))
    rows = pq.read_table(phs.REFERENCE_DISPATCH).to_pylist()
    for row in rows:
        local = row["timestamp_utc"].astimezone(ZONES[row["country"]])
        row.update(local_date=local.date(), local_hour=local.hour,
                   model_year=str(row["timestamp_utc"].year), season=season(local.month),
                   window=window(local.hour), local_fold=local.fold)
    return rows, summaries, reference_audit, p


def grouped_profiles(rows, capacity):
    hourly, periods = defaultdict(list), defaultdict(list)
    for row in rows:
        for year in ("all", row["model_year"]):
            for s in ("all", row["season"]):
                key = row["country"], year, s
                hourly[key + (row["local_hour"],)].append(row)
                periods[key + (row["window"],)].append(row)
    hour_results, period_results = [], []
    for (country, year, s, hour), group in sorted(hourly.items()):
        n = len(group)
        hour_results.append(dict(
            country=country, model_year=year, season=s, local_hour=hour,
            timezone=str(ZONES[country]), observed_hours=n,
            mean_pumping_mw=math.fsum(r["pumping_mw"] for r in group) / n,
            mean_generation_mw=math.fsum(r["generation_mw"] for r in group) / n,
            pumping_active_fraction=sum(r["pumping_mw"] > ACTIVITY_TOL_MW for r in group) / n,
            generation_active_fraction=sum(r["generation_mw"] > ACTIVITY_TOL_MW for r in group) / n,
            mean_soc_begin_fraction=math.fsum(r["energy_begin_mwh"] for r in group) / (n * capacity),
            mean_soc_end_fraction=math.fsum(r["energy_end_mwh"] for r in group) / (n * capacity),
            mean_price_eur_mwh=math.fsum(r["price_eur_mwh"] for r in group) / n,
        ))
    totals = defaultdict(float)
    for key, group in periods.items():
        totals[key[:3]] += math.fsum(r["pumping_mw"] for r in group)
    for (country, year, s, w), group in sorted(periods.items()):
        pumped = math.fsum(r["pumping_mw"] for r in group)
        n = len(group)
        period_results.append(dict(
            country=country, model_year=year, season=s, window=w,
            timezone=str(ZONES[country]), observed_hours=n, pumped_mwh=pumped,
            generated_mwh=math.fsum(r["generation_mw"] for r in group),
            pumping_energy_share=pumped / totals[(country, year, s)],
            mean_pumping_mw=pumped / n,
            pumping_active_hours=sum(r["pumping_mw"] > ACTIVITY_TOL_MW for r in group),
            pumping_active_fraction=sum(r["pumping_mw"] > ACTIVITY_TOL_MW for r in group) / n,
        ))
    return hour_results, period_results


def daily_metrics(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["country"], row["local_date"])].append(row)
    daily = []
    for (country, day), group in sorted(groups.items()):
        group.sort(key=lambda r: r["timestamp_utc"])
        expected = expected_day(day, ZONES[country])
        complete = [r["timestamp_utc"] for r in group] == expected
        energy = {w: math.fsum(r["pumping_mw"] for r in group if r["window"] == w) for w in WINDOWS}
        total = math.fsum(energy.values())
        result = dict(
            country=country, local_date=day, local_year=day.year, season=season(day.month),
            timezone=str(ZONES[country]), complete_local_day=complete,
            observed_hours=len(group), expected_hours=len(expected),
            crosses_model_year=len({r["model_year"] for r in group}) > 1,
            pumped_mwh=total, generated_mwh=math.fsum(r["generation_mw"] for r in group),
            pumping_active_hours=sum(r["pumping_mw"] > ACTIVITY_TOL_MW for r in group),
            energy_begin_mwh=group[0]["energy_begin_mwh"], energy_end_mwh=group[-1]["energy_end_mwh"],
            minimum_energy_mwh=min(min(r["energy_begin_mwh"], r["energy_end_mwh"]) for r in group),
            maximum_energy_mwh=max(max(r["energy_begin_mwh"], r["energy_end_mwh"]) for r in group),
            pattern=classify(energy) if complete else "incomplete_day",
        )
        for w in WINDOWS:
            hours = sum(r["window"] == w for r in group)
            result.update({f"{w}_pumped_mwh": energy[w], f"{w}_observed_hours": hours,
                           f"{w}_energy_share": energy[w] / total if total > DAY_TOL_MWH else None,
                           f"{w}_mean_pumping_mw": energy[w] / hours if hours else None})
        daily.append(result)
    return daily, groups


def pattern_summary(daily):
    result = []
    for country in ZONES:
        for year in ("all", *map(str, phs.STUDY_YEARS)):
            days = [r for r in daily if r["country"] == country and r["complete_local_day"]
                    and (year == "all" or str(r["local_year"]) == year)]
            total = math.fsum(r["pumped_mwh"] for r in days)
            for pattern in PATTERNS:
                selected = [r for r in days if r["pattern"] == pattern]
                pumped = math.fsum(r["pumped_mwh"] for r in selected)
                result.append(dict(country=country, local_year=year, pattern=pattern,
                                   days=len(selected), complete_days=len(days), day_share=len(selected) / len(days),
                                   pumped_mwh=pumped, pumping_energy_share=pumped / total))
    return result


def robustness(daily, groups):
    results = []
    for name, changes in SCENARIOS.items():
        rule = BASE_RULE | changes
        for country in ZONES:
            for year in ("all", *map(str, phs.STUDY_YEARS)):
                days = [r for r in daily if r["country"] == country and r["complete_local_day"]
                        and (year == "all" or str(r["local_year"]) == year)]
                counts, by_type, by_window = Counter(), defaultdict(float), defaultdict(float)
                changed, cross_year = 0, 0
                for day in days:
                    group = groups[(country, day["local_date"])]
                    energy = {w: math.fsum(r["pumping_mw"] for r in group
                                          if window(r["local_hour"], rule) == w) for w in WINDOWS}
                    pattern = classify(energy, rule)
                    counts[pattern] += 1
                    by_type[pattern] += day["pumped_mwh"]
                    changed += pattern != day["pattern"]
                    cross_year += day["crosses_model_year"]
                    for w in WINDOWS:
                        by_window[w] += energy[w]
                total = math.fsum(by_window.values())
                for pattern in PATTERNS:
                    results.append(dict(
                        country=country, local_year=year, scenario=name, **rule,
                        complete_days=len(days), model_boundary_days=cross_year,
                        fraction_days_reclassified=changed / len(days), pattern=pattern,
                        days=counts[pattern], day_share=counts[pattern] / len(days),
                        pumping_energy_share=by_type[pattern] / total,
                        **{f"{w}_energy_share": by_window[w] / total for w in WINDOWS},
                    ))
    return results


def validate_aggregates(rows, annual, hourly, periods, daily, robust):
    def close(a, b):
        assert math.isclose(a, b, rel_tol=0, abs_tol=1e-8), (a, b)

    for a in annual:
        country, year = a["country"], str(int(a["year"]))
        selected = [r for r in periods if (r["country"], r["model_year"], r["season"]) == (country, year, "all")]
        assert len(selected) == 3
        close(sum(r["observed_hours"] for r in selected), a["hours"])
        close(math.fsum(r["pumped_mwh"] for r in selected), a["pumped_mwh"])
        close(math.fsum(r["generated_mwh"] for r in selected), a["generated_mwh"])
        close(sum(r["pumping_energy_share"] for r in selected), 1)
    for country in ZONES:
        selected = [r for r in daily if r["country"] == country]
        close(math.fsum(r["pumped_mwh"] for r in selected),
              math.fsum(r["pumped_mwh"] for r in annual if r["country"] == country))
        complete = [r for r in selected if r["complete_local_day"]]
        assert len(complete) == 1095
        assert Counter(r["observed_hours"] for r in complete) == {23: 3, 24: 1089, 25: 3}
        assert len(selected) - len(complete) == 2
        for r in selected:
            close(sum(r[f"{w}_observed_hours"] for w in WINDOWS), r["observed_hours"])
            close(math.fsum(r[f"{w}_pumped_mwh"] for w in WINDOWS), r["pumped_mwh"])
        for year in ("all", *map(str, phs.STUDY_YEARS)):
            h = [r for r in hourly if (r["country"], r["model_year"], r["season"]) == (country, year, "all")]
            p = [r for r in periods if (r["country"], r["model_year"], r["season"]) == (country, year, "all")]
            assert len(h) == 24
            close(math.fsum(r["mean_pumping_mw"] * r["observed_hours"] for r in h),
                  math.fsum(r["pumped_mwh"] for r in p))
    for summary in pattern_summary(daily):
        r = next(r for r in robust if (r["country"], r["local_year"], r["pattern"], r["scenario"]) ==
                 (summary["country"], summary["local_year"], summary["pattern"], "base"))
        assert r["days"] == summary["days"] and r["fraction_days_reclassified"] == 0
        close(r["pumping_energy_share"], summary["pumping_energy_share"])
    grouped = defaultdict(list)
    for r in robust:
        grouped[(r["country"], r["local_year"], r["scenario"])].append(r)
    for group in grouped.values():
        assert len(group) == len(PATTERNS)
        close(sum(r["day_share"] for r in group), 1)
        close(sum(r["pumping_energy_share"] for r in group), 1)
    assert len(rows) == 52608 and len(daily) == 2194
    print("PASS annual reconciliation, DST coverage, partitions, hourly weighting and robustness summaries", flush=True)


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with path.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert saved == [{k: "" if v is None else str(v) for k, v in r.items()} for r in rows]


def plot_figures(hourly, daily, provenance):
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "hydroforum_matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, font_manager
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.text import Text

    for name in ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"):
        path = Path("/mnt/c/Windows/Fonts") / name
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    try:
        font_manager.findfont("Times New Roman", fallback_to_default=False)
        font = "Times New Roman"
    except ValueError:
        font = "DejaVu Serif"
    plt.rcParams.update({"font.family": font, "font.size": 14, "axes.labelsize": 15,
                         "axes.titlesize": 16, "legend.fontsize": 13,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.axisbelow": True, "axes.linewidth": 0.8,
                         "figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white", "pdf.fonttype": 42})
    figures = []
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.095, right=0.985, bottom=0.10, top=0.88, wspace=0.16, hspace=0.33)
    for i, (ax, s) in enumerate(zip(axes.flat, SEASONS)):
        for country in ZONES:
            group = sorted([r for r in hourly if (r["country"], r["model_year"], r["season"]) ==
                            (country, "all", s)], key=lambda r: r["local_hour"])
            values = [r["mean_pumping_mw"] for r in group]
            line, = ax.plot(range(24), values, color=COLORS[country], linewidth=1.8,
                           marker="o" if country == "PL" else "s", markersize=3.5,
                           linestyle="-" if country == "PL" else "--")
            assert np.array_equal(line.get_ydata(), values)
        ax.set_title(f"({chr(97+i)}) {s.title()}", loc="left", pad=9)
        ax.set_xlim(-0.5, 23.5)
        ax.set_ylim(0, 1)
        ax.set_xticks((0, 6, 12, 18, 23))
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.6)
        if i % 2 == 0:
            ax.set_ylabel("Mean pumping power (MW)")
        if i >= 2:
            ax.set_xlabel("Local hour")
    handles = [Line2D([], [], color=COLORS[c], marker="o" if c == "PL" else "s",
                      linestyle="-" if c == "PL" else "--", label=NAMES[c]) for c in ZONES]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=2, frameon=False)
    figures.append((fig, FIGURES[0], "Mean pumping by local hour and calendar season, 2023-2025. "
                    "Continuous 1 MW, 6 h, 75% model; actual observed hours weighted equally, including DST. "
                    "A mean double peak does not establish two pumping periods within the same day."))

    summary = pattern_summary(daily)
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.10, right=0.985, bottom=0.10, top=0.82, wspace=0.16, hspace=0.30)
    for col, country in enumerate(ZONES):
        for row, metric in enumerate(("day_share", "pumping_energy_share")):
            ax = axes[row, col]
            bottom = np.zeros(3)
            for pattern, color in zip(PATTERNS, PATTERN_COLORS):
                values = np.array([next(r[metric] for r in summary if (r["country"], r["local_year"], r["pattern"]) ==
                                        (country, str(year), pattern)) * 100 for year in phs.STUDY_YEARS])
                bars = ax.bar(range(3), values, bottom=bottom, width=0.59, color=color,
                              edgecolor="#555555", linewidth=0.5, hatch="///" if pattern == "no_pumping" else None)
                assert np.allclose([b.get_height() for b in bars], values, atol=1e-12, rtol=0)
                for x, value, base in zip(range(3), values, bottom):
                    if value >= 8:
                        ax.text(x, base + value / 2, f"{value:.0f}%", ha="center", va="center",
                                color="white" if pattern in ("night_dominant", "both") else "#111111", fontsize=12)
                bottom += values
            assert np.allclose(bottom, 100, atol=1e-10, rtol=0)
            ax.set_title(f"({chr(97 + row*2 + col)}) {NAMES[country]}", loc="left", pad=9)
            ax.set_xticks(range(3), phs.STUDY_YEARS)
            ax.set_ylim(0, 100)
            ax.set_yticks(range(0, 101, 25))
            ax.grid(axis="y", color="#DDDDDD", linewidth=0.6)
            if col == 0:
                ax.set_ylabel("Share of complete days (%)" if row == 0 else "Share of pumping energy (%)")
            if row == 1:
                ax.set_xlabel("Local calendar year")
    handles = [Patch(facecolor=color, edgecolor="#555555", linewidth=0.5,
                     hatch="///" if p == "no_pumping" else None, label=label)
               for p, color, label in zip(PATTERNS, PATTERN_COLORS, PATTERN_LABELS)]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=3, frameon=False)
    figures.append((fig, FIGURES[1], "Daily pumping patterns in complete local calendar days. "
                    "Top: day frequency; bottom: total pumping energy on days of each type, not energy in each window. "
                    "Midday 10:00-16:00; night 22:00-06:00. Both: each >=20% of daily pumping; "
                    "otherwise midday/night-led: >=60%; other is the remainder. No-pumping days included. "
                    "Incomplete boundary days excluded; complete 23/25-hour DST days included."))
    for fig, name, caption in figures:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for text in fig.findobj(Text):
            if text.get_visible() and text.get_text().strip():
                bounds = text.get_window_extent(renderer)
                assert bounds.x0 >= -1 and bounds.y0 >= -1 and bounds.x1 <= fig.bbox.x1 + 1 and bounds.y1 <= fig.bbox.y1 + 1, text.get_text()
        legend_box = fig.legends[0].get_window_extent(renderer)
        assert all(not legend_box.overlaps(ax.get_tightbbox(renderer)) for ax in fig.axes)
        meta = {"Title": name, "Description": caption + " Input SHA-256: " + provenance["input_dispatch_sha256"]}
        fig.savefig(STUDY / "figures" / f"{name}.png", dpi=400, metadata=meta)
        fig.savefig(STUDY / "figures" / f"{name}.pdf", metadata={"Title": name, "Subject": meta["Description"]})
        plt.close(fig)
    return {"font": font, "png_dpi": 400, "captions": {name: caption for _, name, caption in figures}}


def main():
    owned = set(OUTPUTS.values()) | {STUDY / "figures" / f"{name}.{ext}" for name in FIGURES for ext in ("png", "pdf")}
    protected = {p: digest(p) for p in STUDY.rglob("*") if p.is_file() and p not in owned}
    self_checks()
    rows, annual, reference_audit, parameters = load_validated()
    hourly, periods = grouped_profiles(rows, parameters.energy_capacity_mwh)
    daily, groups = daily_metrics(rows)
    robust = robustness(daily, groups)
    validate_aggregates(rows, annual, hourly, periods, daily, robust)
    audit = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "script_sha256": digest(Path(__file__)), "input_dispatch_sha256": digest(phs.REFERENCE_DISPATCH),
        "input_price_sha256": digest(phs.INPUT), "input_annual_sha256": digest(phs.REFERENCE_ANNUAL),
        "parameters": parameters.metadata(), "model_years_utc": list(phs.STUDY_YEARS),
        "timezones": {c: str(z) for c, z in ZONES.items()}, "classification": BASE_RULE,
        "activity_tolerance_mw": ACTIVITY_TOL_MW, "inactive_day_tolerance_mwh": DAY_TOL_MWH,
        "share_comparison_tolerance": SHARE_TOL,
        "reference_validation": reference_audit,
        "method": "No optimisation or clustering. Unrounded energy; actual 1h intervals. Hourly/period summaries "
                  "use original UTC model year and local season/hour. Daily types use complete local calendar days, "
                  "including 23/25h DST days. All-year shares use energy totals, not annual mean shares. "
                  "Both classification takes precedence over dominant. Saved schedule is not necessarily unique.",
        "boundary_days": [{**r, "local_date": r["local_date"].isoformat()} for r in daily if not r["complete_local_day"]],
        "cross_model_year_days": [{"country": r["country"], "local_date": r["local_date"].isoformat(),
                                   "pattern": r["pattern"]} for r in daily if r["crosses_model_year"]],
        "daily_pattern_summary": pattern_summary(daily),
        "patterns_excluding_model_boundary_days": pattern_summary([r for r in daily if not r["crosses_model_year"]]),
        "protected_files_sha256": {str(p.relative_to(STUDY)): h for p, h in protected.items()},
    }
    for country in ZONES:
        total = [r for r in periods if (r["country"], r["model_year"], r["season"]) == (country, "all", "all")]
        audit.setdefault("annual_share_comparison", []).extend([
            dict(country=country, window=r["window"], pooled_share=r["pumping_energy_share"],
                 arithmetic_mean_annual_share=math.fsum(v["pumping_energy_share"] for v in periods
                    if v["country"] == country and v["model_year"] != "all" and v["season"] == "all"
                    and v["window"] == r["window"]) / len(phs.STUDY_YEARS)) for r in total])
        for s in SEASONS:
            h = [r for r in hourly if (r["country"], r["model_year"], r["season"]) == (country, "all", s)]
            peaks = {key: max(h, key=lambda r: r[key])["local_hour"] for key in (
                "mean_pumping_mw", "mean_generation_mw", "mean_soc_end_fraction")}
            audit.setdefault("seasonal_peak_local_hours", []).append(dict(country=country, season=s, **peaks))
    for key, records in (("hourly", hourly), ("period", periods), ("robustness", robust)):
        write_csv(OUTPUTS[key], records)
    table = pa.Table.from_pylist(daily).replace_schema_metadata({b"phs_operating_patterns_provenance": json.dumps(audit).encode()})
    pq.write_table(table, OUTPUTS["daily"], compression="snappy")
    restored = pq.read_table(OUTPUTS["daily"])
    assert restored.equals(table, check_metadata=True)
    audit["figure_checks"] = plot_figures(hourly, restored.to_pylist(), audit)
    audit["output_sha256"] = {str(p.relative_to(STUDY)): digest(p) for p in owned if p != OUTPUTS["audit"]}
    assert all(digest(p) == h for p, h in protected.items()), "Protected study file changed"
    audit["validation_passed"] = True
    OUTPUTS["audit"].write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    assert json.loads(OUTPUTS["audit"].read_text())["validation_passed"]
    print("POOLED WINDOW SHARES", json.dumps(audit["annual_share_comparison"], indent=2))
    print("DAILY TYPES", json.dumps([r for r in audit["daily_pattern_summary"] if r["local_year"] == "all"], indent=2))
    print(f"PASS readback and figure geometry; {len(protected)} existing study files unchanged", flush=True)


if __name__ == "__main__":
    main()
