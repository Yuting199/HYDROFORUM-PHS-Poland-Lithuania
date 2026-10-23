#!/usr/bin/env python3
"""Draw two publication figures using only saved annual price diagnostics."""

import csv
import hashlib
import math
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "hydroforum_matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.text import Text


STUDY = Path(__file__).resolve().parents[1]
SOURCE = STUDY / "data/price_spread_diagnostics_2021_2025.csv"
COUNTRIES = {"PL": "Poland", "LT": "Lithuania"}
YEARS = tuple(range(2021, 2026))
HIGH = "#D55E00"
LOW = "#0072B2"
FIELDS = {
    "endpoint": ("mean_daily_min_eur_mwh", "mean_daily_max_eur_mwh", "mean_daily_spread_eur_mwh"),
    "six_hour": ("mean_daily_low_six_eur_mwh", "mean_daily_high_six_eur_mwh",
                 "mean_daily_six_hour_band_spread_eur_mwh"),
}
BAR_CASES = ((2021, 2025, "endpoint"), (2021, 2025, "six_hour"),
             (2023, 2025, "endpoint"), (2023, 2025, "six_hour"))


def load_diagnostics():
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    rows = {(r["country"], int(r["year"])): r for r in records}
    if len(records) != 10 or set(rows) != {(c, y) for c in COUNTRIES for y in YEARS}:
        raise ValueError("Expected exactly one row per country/year in 2021-2025")
    for row in rows.values():
        for field in {f for columns in FIELDS.values() for f in columns}:
            row[field] = float(row[field])
            if not math.isfinite(row[field]):
                raise ValueError(f"Nonfinite diagnostic: {field}")
        for low, high, spread in FIELDS.values():
            if not math.isclose(row[high] - row[low], row[spread], rel_tol=0, abs_tol=1e-10):
                raise ValueError("Annual spread identity failed")
        if not (row[FIELDS['endpoint'][0]] <= row[FIELDS['six_hour'][0]]
                <= row[FIELDS['six_hour'][1]] <= row[FIELDS['endpoint'][1]]):
            raise ValueError("Annual price bands are not ordered")
    return rows


def configure_style():
    for name in ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"):
        path = Path("/mnt/c/Windows/Fonts") / name
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    print("Font:", font_manager.findfont("Times New Roman", fallback_to_default=False))
    plt.rcParams.update({
        "font.family": "Times New Roman", "font.size": 18,
        "axes.titlesize": 21, "axes.labelsize": 19,
        "xtick.labelsize": 17, "ytick.labelsize": 17, "legend.fontsize": 17,
        "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 0.9,
        "axes.axisbelow": True, "figure.facecolor": "white", "axes.facecolor": "white",
        "savefig.facecolor": "white", "text.color": "#202020", "axes.labelcolor": "#202020",
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def axes_pair(top, bottom=0.16):
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), sharey=True)
    fig.subplots_adjust(left=0.09, right=0.985, bottom=bottom, top=top, wspace=0.16)
    for index, (ax, country) in enumerate(zip(axes, COUNTRIES.values())):
        ax.set_title(f"({chr(97 + index)}) {country}", loc="left", pad=13)
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.65)
        ax.tick_params(axis="both", length=4, pad=6)
    return fig, axes


def price_structure(rows):
    fig, axes = axes_pair(top=0.76)
    series = (
        (FIELDS["endpoint"][1], "Daily maximum", HIGH, "--", "^", True),
        (FIELDS["six_hour"][1], "Six most expensive hours", HIGH, "-", "^", False),
        (FIELDS["endpoint"][0], "Daily minimum", LOW, "--", "o", True),
        (FIELDS["six_hour"][0], "Six cheapest hours", LOW, "-", "o", False),
    )
    for ax, country in zip(axes, COUNTRIES):
        upper = [rows[country, y][FIELDS["six_hour"][1]] for y in YEARS]
        lower = [rows[country, y][FIELDS["six_hour"][0]] for y in YEARS]
        ax.fill_between(YEARS, lower, upper, color="#999999", alpha=0.13, linewidth=0)
        for field, label, color, style, marker, endpoint in series:
            values = [rows[country, year][field] for year in YEARS]
            line, = ax.plot(YEARS, values, label=label, color=color, linestyle=style,
                            marker=marker, markersize=7.5, linewidth=1.6 if endpoint else 2.3,
                            alpha=0.6 if endpoint else 1.0,
                            markerfacecolor="white" if endpoint else color, markeredgewidth=1.0)
            if list(line.get_ydata()) != values:
                raise ValueError("Plotted annual series differs from saved diagnostics")
        ax.set_xticks(YEARS)
        ax.set_xlim(2020.75, 2025.25)
        ax.set_xlabel("Year", labelpad=9)
    all_prices = [r[field] for r in rows.values() for field, *_ in series]
    lower_limit = min(0, math.floor(min(all_prices) / 50) * 50)
    upper_limit = math.ceil(max(all_prices) / 50) * 50
    axes[0].set_ylim(lower_limit, upper_limit)
    axes[0].set_yticks(range(lower_limit, upper_limit + 1, 50))
    axes[0].set_ylabel("EUR/MWh", labelpad=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.54, 1),
               ncol=2, frameon=False, columnspacing=2.5, handlelength=2.5)
    return fig


def change_components(rows, country, start, end, definition):
    low, high, spread = FIELDS[definition]
    a, b = rows[country, start], rows[country, end]
    components = b[high] - a[high], a[low] - b[low]
    total = b[spread] - a[spread]
    if not math.isclose(math.fsum(components), total, rel_tol=0, abs_tol=1e-10):
        raise ValueError("Signed contributions do not sum to the saved spread change")
    return components, total


def signed_segments(components):
    positive, negative = 0.0, 0.0
    bottoms = []
    for value in components:
        bottoms.append(positive if value >= 0 else negative)
        if value >= 0:
            positive += value
        else:
            negative += value
    return bottoms, positive, negative


def spread_contributions(rows):
    # Opposing signs must extend on opposite sides of zero, never cancel visually.
    if signed_segments((10, -4)) != ([0.0, 0.0], 10.0, -4.0):
        raise ValueError("Signed stacking check failed")
    fig, axes = axes_pair(top=0.81, bottom=0.22)
    positions = (0, 1, 2.5, 3.5)
    all_tops, all_bottoms = [], []
    for ax, country in zip(axes, COUNTRIES):
        for x, (start, end, definition) in zip(positions, BAR_CASES):
            components, total = change_components(rows, country, start, end, definition)
            bottoms, positive, negative = signed_segments(components)
            all_tops.append(positive)
            all_bottoms.append(negative)
            for value, bottom, color in zip(components, bottoms, (HIGH, LOW)):
                bar = ax.bar(x, value, bottom=bottom, width=0.68, color=color,
                             edgecolor="white", linewidth=0.7)[0]
                if not (math.isclose(bar.get_height(), value, rel_tol=0, abs_tol=1e-10)
                        and math.isclose(bar.get_y(), bottom, rel_tol=0, abs_tol=1e-10)):
                    raise ValueError("Plotted contribution differs from calculated value")
                ax.text(x, bottom + value / 2, f"{value:+.2f}", ha="center", va="center",
                        fontsize=15, color="white")
            edge = positive if total >= 0 else negative
            ax.annotate(f"{total:+.2f}", (x, edge), xytext=(0, 7 if total >= 0 else -7),
                        textcoords="offset points", ha="center", va="bottom" if total >= 0 else "top",
                        fontsize=16, fontweight="bold")
            if min(components) < 0 < max(components):
                ax.plot(x, total, marker="D", color="#202020", markersize=5)
            print(f"{country} {start}-{end} {definition}: "
                  f"high={components[0]:+.8f}, low={components[1]:+.8f}, total={total:+.8f}")
        ax.axhline(0, color="#333333", linewidth=0.9)
        ax.set_xticks(positions, [f"{a}-{b}\n{'Endpoint' if d == 'endpoint' else '6-hour'}"
                                 for a, b, d in BAR_CASES])
        ax.tick_params(axis="x", labelsize=15.5, pad=10, length=0)
        ax.set_xlim(-0.65, 4.15)
    lower = min(0, math.floor((min(all_bottoms) - 8) / 20) * 20) if min(all_bottoms) < 0 else 0
    upper = math.ceil((max(all_tops) + 12) / 20) * 20
    axes[0].set_ylim(lower, upper)
    axes[0].set_yticks(range(lower, upper + 1, 20))
    axes[0].set_ylabel("Change in spread (EUR/MWh)", labelpad=10)
    fig.legend(handles=[Patch(facecolor=HIGH, label="High-price contribution"),
                        Patch(facecolor=LOW, label="Low-price contribution")],
               loc="upper center", bbox_to_anchor=(0.54, 0.99), ncol=2,
               frameon=False, columnspacing=2, handlelength=1.3)
    return fig


def save_figure(fig, filename, title, caption, digest):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for text in fig.findobj(match=Text):
        if text.get_visible() and text.get_text():
            bounds = text.get_window_extent(renderer)
            if bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > fig.bbox.width or bounds.y1 > fig.bbox.height:
                raise ValueError(f"Text outside figure bounds: {text.get_text()}")
    provenance = f"{caption} Source: {SOURCE.name}; SHA-256: {digest}."
    for extension in ("png", "pdf"):
        path = STUDY / "figures" / f"{filename}.{extension}"
        metadata = {"Title": title, "Description": provenance} if extension == "png" else {
            "Title": title, "Subject": provenance, "Creator": "Matplotlib; plot_price_spread_figures.py"}
        fig.savefig(path, dpi=600, bbox_inches="tight", pad_inches=0.12, metadata=metadata)
        print("Saved", path)
    plt.close(fig)


def main():
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    rows = load_diagnostics()
    configure_style()
    save_figure(price_structure(rows), "figure_price_structure",
                "Price structure in Poland and Lithuania, 2021-2025",
                "Annual means over complete UTC days. The six-hour band is defined from the mean "
                "of the six most expensive and six cheapest hourly prices within each complete UTC day. "
                "Shading is a visual aid, not a confidence interval or storage profit.", digest)
    save_figure(spread_contributions(rows), "figure_spread_contributions",
                "Contributions of high and low prices to spread widening",
                "High-price contribution = H_b - H_a; low-price contribution = L_a - L_b. "
                "Bold labels show total spread changes; segment labels show signed contributions "
                "in EUR/MWh. Labels are rounded to two decimals; geometry uses unrounded values. "
                "Six-hour price bands are descriptive, not feasible storage schedules.", digest)
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != digest:
        raise ValueError("Source diagnostics changed while plotting")
    print("PASS: 40 annual plotted values match diagnostics exactly; all 8 contribution sums validated.")


if __name__ == "__main__":
    main()
