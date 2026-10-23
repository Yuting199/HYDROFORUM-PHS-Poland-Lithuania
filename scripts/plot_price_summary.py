#!/usr/bin/env python3
"""Plot annual PL/LT price summaries from the cleaned hourly inspection data."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from statistics import fmean
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "hydroforum_matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.text import Text
import pyarrow.parquet as pq

from plot_main_figures import COUNTRY_COLORS

STUDY = Path(__file__).resolve().parents[1]
YEARS = range(2021, 2026)
COUNTRIES = {
    "PL": {"name": "Poland", "color": COUNTRY_COLORS["PL"], "marker": "o", "linestyle": "-"},
    "LT": {"name": "Lithuania", "color": COUNTRY_COLORS["LT"], "marker": "s", "linestyle": "--"},
}
CAPTION = ("Figure 1. Day-ahead electricity price indicators in Poland and Lithuania, "
           "2021\u20132025: (a) negative-price hours, (b) annual mean price, and (c) annual mean "
           "daily maximum\u2013minimum price spread. Source: ENTSO-E.")


def annual_statistics():
    source = STUDY / "data/prices_PL_LT_hourly_2021_2025.parquet"
    rows = pq.read_table(source).to_pylist()
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    if len(rows) != 43824 or any(
        row["timestamp_utc"] != start + timedelta(hours=i) for i, row in enumerate(rows)
    ):
        raise ValueError("Expected the complete 2021-2025 UTC hourly index")
    results = {}
    for zone in COUNTRIES:
        results[zone] = []
        for year in YEARS:
            annual = [r for r in rows if r["timestamp_utc"].year == year]
            prices = [r[f"{zone}_EUR_MWh"] for r in annual]
            if any(p is None for p in prices):
                raise ValueError(f"Coverage changed for {zone} {year}; review before plotting")
            days = defaultdict(list)
            for row in annual:
                days[row["timestamp_utc"].date()].append(row[f"{zone}_EUR_MWh"])
            spreads = [max(p) - min(p) for p in days.values() if len(p) == 24]
            expected_days = 366 if year == 2024 else 365
            if len(spreads) != expected_days:
                raise ValueError(f"Incomplete UTC days for {zone} {year}")
            results[zone].append({
                "year": year, "mean_price": fmean(prices), "daily_spread": fmean(spreads),
                "negative_hours": sum(p < 0 for p in prices), "complete_days": len(spreads),
            })
    return results


def configure_style():
    for filename in ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"):
        path = Path("/mnt/c/Windows/Fonts") / filename
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    font_path = font_manager.findfont("Times New Roman", fallback_to_default=False)
    plt.rcParams.update({
        "font.family": "Times New Roman", "font.size": 18,
        "axes.titlesize": 21, "axes.labelsize": 19,
        "xtick.labelsize": 17, "ytick.labelsize": 17, "legend.fontsize": 17,
        "axes.linewidth": 0.9, "axes.spines.top": False, "axes.spines.right": False,
        "axes.axisbelow": True, "text.color": "#202020", "axes.labelcolor": "#202020",
        "xtick.color": "#202020", "ytick.color": "#202020",
        "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.facecolor": "white", "figure.facecolor": "white", "axes.facecolor": "white",
    })
    print(f"Font: {font_path}")


def main():
    source = STUDY / "data/prices_PL_LT_hourly_2021_2025.parquet"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    stats = annual_statistics()
    configure_style()
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6))
    fig.subplots_adjust(left=0.064, right=0.985, bottom=0.17, top=0.77, wspace=0.33)
    panels = [
        ("negative_hours", "(a) Negative-price hours", "Hours/year", 350),
        ("mean_price", "(b) Mean price", "EUR/MWh", 270),
        ("daily_spread", "(c) Mean daily spread", "EUR/MWh", 270),
    ]
    bar_labels = []
    for ax, (metric, title, unit, maximum) in zip(axes, panels):
        for index, (zone, style) in enumerate(COUNTRIES.items()):
            values = [r[metric] for r in stats[zone]]
            if metric == "negative_hours":
                positions = [year + (-0.21 if index == 0 else 0.21) for year in YEARS]
                bars = ax.bar(
                    positions, values, width=0.36, color=style["color"],
                    edgecolor="white", linewidth=0.6, hatch="///" if index else None,
                )
                if [bar.get_height() for bar in bars] != values:
                    raise ValueError("Negative-price bars differ from original statistics")
                bar_labels.extend(ax.bar_label(bars, labels=[str(value) for value in values], padding=5, fontsize=14))
            else:
                line, = ax.plot(
                    list(YEARS), values, color=style["color"], marker=style["marker"],
                    linestyle=style["linestyle"], linewidth=2.0, markersize=7,
                    markeredgecolor="white", markeredgewidth=0.8,
                )
                if list(line.get_ydata()) != values:
                    raise ValueError("Price lines differ from original statistics")
        ax.set_title(title, loc="left", pad=18)
        ax.set_ylabel(unit, labelpad=9)
        ax.set_xlabel("Year", labelpad=9)
        ax.set_xticks(list(YEARS))
        ax.set_xlim(2020.5, 2025.5)
        ax.set_ylim(0, maximum)
        ax.set_yticks(range(0, maximum + 1, 50))
        ax.grid(axis="y", color="#D9D9D9", linewidth=0.65)
        ax.tick_params(axis="both", length=4, pad=6)

    handles = [
        Line2D([], [], color=s["color"], marker=s["marker"], linestyle=s["linestyle"],
               linewidth=2.0, markersize=7, label=s["name"])
        for s in COUNTRIES.values()
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.52, 0.985),
               ncol=2, frameon=False, handlelength=2.3, columnspacing=2.5)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    boxes = [label.get_window_extent(renderer) for label in bar_labels]
    if any(a.overlaps(b) for index, a in enumerate(boxes) for b in boxes[index + 1:]):
        raise ValueError("Negative-price bar labels overlap")
    for label in fig.findobj(match=Text):
        if label.get_visible() and label.get_text():
            box = label.get_window_extent(renderer)
            if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
                raise ValueError(f"Clipped label: {label.get_text()}")
    if axes[1].get_ylim() != axes[2].get_ylim():
        raise ValueError("Price panels must share the same scale")
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ValueError("Price input changed during plotting")
    description = CAPTION + " Source SHA-256: " + source_hash + "; plotted statistics: " + json.dumps(stats, sort_keys=True)
    output = STUDY / "figures/figure_1_market_background"
    output.parent.mkdir(exist_ok=True)
    for suffix in ("png", "pdf"):
        path = output.with_suffix(f".{suffix}")
        metadata = {"Title": "Market background", "Description": description} if suffix == "png" else {
            "Title": "Market background", "Subject": description, "Creator": "Matplotlib; plot_price_summary.py"}
        fig.savefig(path, dpi=600, bbox_inches="tight", pad_inches=0.12, metadata=metadata)
        print(f"Saved {path}")
    plt.close(fig)
    for zone, annual in stats.items():
        for row in annual:
            print(f"{zone} {row['year']}: mean={row['mean_price']:.2f}, "
                  f"spread={row['daily_spread']:.2f}, negative={row['negative_hours']}, "
                  f"complete_days={row['complete_days']}")


if __name__ == "__main__":
    main()
