#!/usr/bin/env python3
"""Plot revised technical and arbitrage-only CAPEX figures from saved outputs."""

import hashlib
import json

import plot_main_figures as previous
from matplotlib import pyplot as plt
from matplotlib.patches import Patch
from matplotlib.text import Text


STUDY = previous.STUDY
COLORS = previous.COUNTRY_COLORS
COUNTRIES = previous.COUNTRIES
MARKERS = {"PL": "o", "LT": "s"}
REFERENCE_CAPEX = 2500.0
SOURCES = [previous.SOURCES[k] for k in ("technical", "financial", "baseline")]


def technical_sensitivity(means):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 5.2), sharey=True)
    fig.subplots_adjust(left=0.13, right=0.98, bottom=0.18, top=0.74, wspace=0.18)
    panels = (
        (("dur4", "base", "dur8"), (4, 6, 8), ("4 h", "6 h", "8 h"),
         "(a) Storage-duration sensitivity", "75% round-trip efficiency", "Storage duration"),
        (("eta70", "base", "eta80"), (70, 75, 80), ("70%", "75%", "80%"),
         "(b) Efficiency sensitivity", "6 h storage duration", "Round-trip efficiency"),
    )
    for ax, (configs, positions, ticks, title, subtitle, xlabel) in zip(axes, panels):
        ax.set_title(title, loc="left", fontsize=18, pad=28)
        ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=13, color="#555555", va="bottom")
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.65)
        for country, label in COUNTRIES.items():
            rows = [means[country, config] for config in configs]
            # Select subsets by the saved actual parameters, not labels alone.
            actual = [float(row["duration_hours" if configs[0] == "dur4" else "efficiency"]) for row in rows]
            expected = list(positions) if configs[0] == "dur4" else [x / 100 for x in positions]
            if actual != expected:
                raise ValueError("Incorrect technical sensitivity subset")
            values = [r[previous.MARGIN] for r in rows]
            line, = ax.plot(positions, values, color=COLORS[country], marker=MARKERS[country],
                            linewidth=2, markersize=6.5, label=label, zorder=3)
            if list(line.get_ydata()) != values:
                raise ValueError("Technical points differ from saved mean arbitrage margins")
            ax.plot(positions[1], values[1], linestyle="none", marker=MARKERS[country],
                    markersize=11, markerfacecolor="none", markeredgecolor=COLORS[country],
                    markeredgewidth=1.2, zorder=4)
            for index, (x, value) in enumerate(zip(positions, values)):
                ax.annotate(f"{value:.1f}", (x, value), xytext=(0, 10), textcoords="offset points",
                            ha="center", va="bottom", fontsize=15,
                            fontweight="bold" if index == 1 else "normal")
        ax.set_xticks(positions, ticks)
        ax.set_xlabel(xlabel, fontsize=17, labelpad=9)
        padding = (positions[-1] - positions[0]) * 0.17
        ax.set_xlim(positions[0] - padding, positions[-1] + padding)
        ax.tick_params(axis="both", length=4, pad=6, labelsize=16)
    axes[0].set_ylim(80, 225)
    axes[0].set_yticks([100, 125, 150, 175, 200])
    axes[0].set_ylabel("Mean arbitrage margin\n(EUR/kW/year)", fontsize=17, labelpad=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.55, 1),
               ncol=2, frameon=False, handlelength=2.5, columnspacing=2.5)
    return fig


def supportable_capex(finance):
    selected = {(country, rate): row for (country, rate, alpha), row in finance.items() if alpha == 1}
    if len(selected) != 6 or any(row["configuration_id"] != "base" or int(row["alpha"]) != 1
                                  for row in selected.values()):
        raise ValueError("CAPEX chart must contain only baseline arbitrage-only results")
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    fig.subplots_adjust(left=0.13, right=0.98, bottom=0.17, top=0.79)
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.65)
    centers, width = (0, 1.15, 2.3), 0.30
    gap_annotations = []
    for index, country in enumerate(COUNTRIES):
        for center, rate in zip(centers, previous.RATES):
            row = selected[country, rate]
            if float(row["reference_capex_eur_per_kw"]) != REFERENCE_CAPEX:
                raise ValueError("Unexpected reference CAPEX")
            value = row["supportable_capex_eur_per_kw"]
            gap = value - REFERENCE_CAPEX
            x = center + (index - 0.5) * (width + 0.06)
            bar = ax.bar(x, value, width=width, color=COLORS[country], edgecolor="white", linewidth=0.8)[0]
            previous.check_bar(bar, value)
            if abs(gap + REFERENCE_CAPEX - value) > 1e-10:
                raise ValueError("Signed capital-cost gap identity failed")
            ax.annotate(f"{value:,.0f}", (x, value), xytext=(0, 2), textcoords="offset points",
                        ha="center", va="bottom", fontsize=15,
                        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4})
            gap_label = f"Gap:\n{gap:+,.0f}".replace("-", "\u2212")
            annotation = ax.annotate(gap_label, (x, value), xytext=(0, -9), textcoords="offset points",
                                     ha="center", va="top", fontsize=12, color="white", linespacing=1.1)
            gap_annotations.append((bar, annotation))
            print(f"{country} {rate:.0%}: supportable={value:.8f}, signed_CAPEX_gap={gap:+.8f}")
    ax.axhline(REFERENCE_CAPEX, color="#555555", linestyle="--", linewidth=1.2)
    ax.annotate("Reference: EUR 2,500/kW", (0.98, REFERENCE_CAPEX),
                xycoords=("axes fraction", "data"), xytext=(0, 5), textcoords="offset points",
                ha="right", va="bottom", fontsize=13, color="#444444")
    ax.set_xticks(centers, [f"{r:.0%}" for r in previous.RATES])
    ax.set_xlabel("Discount rate", fontsize=17, labelpad=9)
    ax.set_ylabel("Supportable CAPEX (EUR/kW)", fontsize=17, labelpad=10)
    ax.set_xlim(-0.65, 2.95)
    ax.set_ylim(0, 2850)
    ax.set_yticks(range(0, 2501, 500))
    ax.tick_params(axis="both", length=4, pad=6, labelsize=16)
    fig.legend(handles=[Patch(facecolor=COLORS[c], label=name) for c, name in COUNTRIES.items()],
               loc="upper center", bbox_to_anchor=(0.55, 1), ncol=2, frameon=False, columnspacing=2.5)
    fig.text(0.55, 0.88, "Baseline: 6 h, 75% efficiency; arbitrage only", ha="center", fontsize=13, color="#555555")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for bar, annotation in gap_annotations:
        label_box, bar_box = annotation.get_window_extent(renderer), bar.get_window_extent(renderer)
        if (label_box.x0 < bar_box.x0 + 2 or label_box.x1 > bar_box.x1 - 2
                or label_box.y0 < bar_box.y0 or label_box.y1 > bar_box.y1):
            raise ValueError("Signed gap label does not fit inside its bar")
    return fig


def save(fig, name, title, note, hashes):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for item in fig.findobj(match=Text):
        if item.get_visible() and item.get_text():
            box = item.get_window_extent(renderer)
            if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
                raise ValueError(f"Clipped text: {item.get_text()}")
    description = note + " Source SHA-256: " + json.dumps(hashes, sort_keys=True)
    for extension in ("png", "pdf"):
        metadata = {"Title": title, "Description": description} if extension == "png" else {
            "Title": title, "Subject": description, "Creator": "Matplotlib; plot_revised_main_figures.py"}
        path = STUDY / "figures" / f"{name}.{extension}"
        fig.savefig(path, dpi=600, bbox_inches="tight", pad_inches=0.12, metadata=metadata)
        print("Saved", path, flush=True)
    plt.close(fig)


def main():
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
    previous.price_plot.configure_style()
    means, finance = previous.load_results()
    save(technical_sensitivity(means), "figure_1_technical_sensitivity_revised",
         "Sensitivity of arbitrage margins to storage duration and efficiency",
         "Arithmetic mean of independent 2023-2025 annual MILPs. Panel (a) fixes efficiency at 75%; "
         "panel (b) fixes duration at 6 h. Outline rings and bold labels mark the shared baseline. "
         "The shared line-chart y-axis begins at 80 EUR/kW/year. Values are market arbitrage margins "
         "without fixed O&M; continuous pumping retains binary pumping/generation exclusivity.", hashes)
    save(supportable_capex(finance), "figure_2_supportable_capex_arbitrage_only",
         "Supportable CAPEX from day-ahead arbitrage only",
         "Baseline: 6 h, 75% efficiency, arbitrage only, 40-year lifetime and proportional fixed O&M "
         "of 2.2% per year. Signed gap = supportable CAPEX - EUR 2,500/kW; it is a capital-cost gap, "
         "not an annual revenue requirement. Labels rounded to whole EUR/kW; geometry and gaps use "
         "unrounded results. Historical reported-EUR margins held level without price-year rebasing.", hashes)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[p.name] for p in SOURCES):
        raise ValueError("Saved source changed during plotting")
    print("PASS: 12 technical points (10 unique means), 6 arbitrage-only CAPEX bars and 6 signed gaps checked.")


if __name__ == "__main__":
    main()
