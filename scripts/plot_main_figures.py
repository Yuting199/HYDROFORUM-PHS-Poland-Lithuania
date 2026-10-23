#!/usr/bin/env python3
"""Plot the three main study figures from saved results, without optimisation."""

import csv
import hashlib
import json
import math
from pathlib import Path

import plot_price_spread_figures as price_plot
from matplotlib import pyplot as plt
from matplotlib.patches import Patch
from matplotlib.text import Text


STUDY = Path(__file__).resolve().parents[1]
SOURCES = {
    "prices": STUDY / "data/price_spread_diagnostics_2021_2025.csv",
    "technical": STUDY / "data/phs_sensitivity_mean.csv",
    "financial": STUDY / "data/phs_financial_sensitivity.csv",
    "baseline": STUDY / "data/phs_baseline_annual.csv",
}
CONFIGURATIONS = (("dur4", 4, 0.75), ("base", 6, 0.75), ("dur8", 8, 0.75),
                  ("eta70", 6, 0.70), ("eta80", 6, 0.80))
COUNTRIES = {"PL": "Poland", "LT": "Lithuania"}
COUNTRY_COLORS = {"PL": "#0072B2", "LT": "#009E73"}
RATES = (0.05, 0.07, 0.09)
MARGIN = "mean_margin_eur_per_kw_year"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_results():
    technical = read_csv(SOURCES["technical"])
    financial = read_csv(SOURCES["financial"])
    baseline = read_csv(SOURCES["baseline"])
    means = {(r["country"], r["configuration_id"]): r for r in technical}
    finance = {(r["country"], float(r["discount_rate"]), int(r["alpha"])): r for r in financial}
    if len(technical) != 10 or set(means) != {(c, k) for c in COUNTRIES for k, _, _ in CONFIGURATIONS}:
        raise ValueError("Expected exactly 10 unique technical means")
    if len(financial) != 12 or set(finance) != {(c, rate, a) for c in COUNTRIES for rate in RATES for a in (1, 2)}:
        raise ValueError("Expected exactly 12 unique baseline financial cases")
    if len(baseline) != 6 or {(r["country"], int(r["year"])) for r in baseline} != {
            (c, y) for c in COUNTRIES for y in (2023, 2024, 2025)}:
        raise ValueError("Unexpected original baseline cases")
    for country in COUNTRIES:
        for config, duration, efficiency in CONFIGURATIONS:
            row = means[country, config]
            expected = {"duration_hours": duration, "efficiency": efficiency, "years": 3,
                        "generating_power_mw": 1, "pumping_power_mw": 1,
                        "energy_capacity_mwh": duration, "initial_soc_fraction": 0.5}
            if row["pumping_formulation"] != "continuous" or any(float(row[k]) != v for k, v in expected.items()):
                raise ValueError("Technical configuration mismatch")
            row[MARGIN] = float(row[MARGIN])
            if not math.isfinite(row[MARGIN]) or row[MARGIN] < 0:
                raise ValueError("Invalid plotted margin")
        original = [float(r["margin_eur_per_kw_year"]) for r in baseline if r["country"] == country]
        if not math.isclose(math.fsum(original) / len(original), means[country, "base"][MARGIN], rel_tol=0, abs_tol=1e-10):
            raise ValueError("Baseline mean does not reconcile with original annual results")
    for (country, rate, alpha), row in finance.items():
        if row["configuration_id"] != "base" or any(float(row[k]) != v for k, v in {
                "lifetime_years": 40, "reference_capex_eur_per_kw": 2500,
                "fixed_om_fraction_per_year": 0.022}.items()):
            raise ValueError("Figure 3 must use the agreed baseline financial assumptions")
        for key in (MARGIN, "supportable_capex_eur_per_kw", "remaining_net_revenue_requirement_eur_per_kw_year",
                    "annual_cost_requirement_eur_per_kw_year", "annual_surplus_eur_per_kw_year"):
            row[key] = float(row[key])
            if not math.isfinite(row[key]):
                raise ValueError("Nonfinite financial result")
        if row[MARGIN] != means[country, "base"][MARGIN]:
            raise ValueError("Financial margin differs from the 6 h, 75% technical baseline")
        factor = rate / (1 - (1 + rate) ** -40) + 0.022
        expected = {
            "supportable_capex_eur_per_kw": alpha * row[MARGIN] / factor,
            "annual_cost_requirement_eur_per_kw_year": 2500 * factor,
            "annual_surplus_eur_per_kw_year": alpha * row[MARGIN] - 2500 * factor,
            "remaining_net_revenue_requirement_eur_per_kw_year": max(0, 2500 * factor - alpha * row[MARGIN]),
        }
        if any(not math.isclose(row[k], v, rel_tol=0, abs_tol=1e-9) for k, v in expected.items()):
            raise ValueError("Saved financial identities failed")
    return means, finance


def check_bar(bar, value):
    if not math.isclose(bar.get_height(), value, rel_tol=0, abs_tol=1e-10):
        raise ValueError("Plotted bar differs from saved result")


def technical_figure(means):
    fig, axes = price_plot.axes_pair(top=0.89, bottom=0.19)
    fig.subplots_adjust(left=0.115)
    for ax, country in zip(axes, COUNTRIES):
        baseline = means[country, "base"][MARGIN]
        ax.axhline(baseline, color="#777777", linestyle="--", linewidth=0.9, zorder=1)
        for x, (config, _, _) in enumerate(CONFIGURATIONS):
            value = means[country, config][MARGIN]
            bar = ax.bar(x, value, width=0.64, color="#404A50" if config == "base" else "#AFBBC2",
                         edgecolor="white", linewidth=0.8, zorder=2)[0]
            check_bar(bar, value)
            ax.annotate(f"{value:.1f}", (x, value), xytext=(0, 5), textcoords="offset points",
                        ha="center", va="bottom", fontsize=16, fontweight="bold" if config == "base" else "normal",
                        bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4})
        ax.set_xticks(range(len(CONFIGURATIONS)), [f"{d} h\n{eta:.0%}" for _, d, eta in CONFIGURATIONS])
        ax.get_xticklabels()[1].set_fontweight("bold")
        ax.tick_params(axis="x", length=0, pad=10, labelsize=16)
        ax.set_xlim(-0.65, len(CONFIGURATIONS) - 0.35)
    limit = math.ceil((max(r[MARGIN] for r in means.values()) + 25) / 50) * 50
    axes[0].set_ylim(0, limit)
    axes[0].set_yticks(range(0, limit + 1, 50))
    axes[0].set_ylabel("Mean arbitrage margin\n(EUR/kW/year)", labelpad=10)
    return fig


def financial_figure(finance):
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    fig.subplots_adjust(left=0.095, right=0.985, bottom=0.16, top=0.76, wspace=0.36)
    keys = ("supportable_capex_eur_per_kw", "remaining_net_revenue_requirement_eur_per_kw_year")
    titles = ("(a) Supportable CAPEX", "(b) Remaining revenue requirement")
    labels = ("Supportable CAPEX (EUR/kW)", "Remaining annual net revenue\nrequirement (EUR/kW/year)")
    groups = [(c, a) for c in COUNTRIES for a in (1, 2)]
    centers, width = (0, 1.35, 2.7), 0.22
    for ax, key, title, ylabel in zip(axes, keys, titles, labels):
        ax.set_title(title, loc="left", pad=13, fontsize=19)
        ax.set_ylabel(ylabel, labelpad=9, fontsize=17)
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.65)
        for i, (country, alpha) in enumerate(groups):
            color = COUNTRY_COLORS[country]
            for center, rate in zip(centers, RATES):
                x = center + (i - 1.5) * width
                value = finance[country, rate, alpha][key]
                bar = ax.bar(x, value, width=width * 0.90, facecolor=color if alpha == 1 else "white",
                             edgecolor=color, hatch="///" if alpha == 2 else None, linewidth=1.1, zorder=3)[0]
                check_bar(bar, value)
                if value == 0:
                    ax.plot([x - width * 0.45, x + width * 0.45], [0, 0], color=color,
                            linewidth=2, clip_on=False, zorder=4)
                label = f"{value:,.0f}" if key == keys[0] else f"{value:.1f}"
                ax.annotate(label, (x, value), xytext=(0, 4), textcoords="offset points",
                            ha="center", va="bottom", fontsize=12.5, zorder=5,
                            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.4})
        ax.set_xticks(centers, [f"{r:.0%}" for r in RATES])
        ax.set_xlabel("Discount rate", labelpad=9)
        ax.set_xlim(-0.66, 3.36)
        ax.tick_params(axis="both", length=4, pad=6)
    axes[0].set_ylim(0, 5200)
    axes[0].set_yticks([0, 1000, 2000, 2500, 3000, 4000, 5000])
    axes[0].axhline(2500, color="#555555", linewidth=1.1, linestyle="--", zorder=2)
    axes[1].set_ylim(0, 210)
    axes[1].set_yticks(range(0, 201, 50))
    handles = [Patch(facecolor=COUNTRY_COLORS[c] if a == 1 else "white", edgecolor=COUNTRY_COLORS[c],
                     hatch="///" if a == 2 else None,
                     label=f"{COUNTRIES[c]}: {'Arbitrage only' if a == 1 else '2m'}") for c, a in groups]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.54, 1.0),
               ncol=2, frameon=False, columnspacing=2.2, handlelength=1.5)
    return fig


def save(fig, name, title, caption, hashes):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for item in fig.findobj(match=Text):
        if item.get_visible() and item.get_text():
            bounds = item.get_window_extent(renderer)
            if bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > fig.bbox.width or bounds.y1 > fig.bbox.height:
                raise ValueError(f"Clipped text: {item.get_text()}")
    description = caption + " Source SHA-256: " + json.dumps(hashes, sort_keys=True)
    for extension in ("png", "pdf"):
        metadata = {"Title": title, "Description": description} if extension == "png" else {
            "Title": title, "Subject": description, "Creator": "Matplotlib; plot_main_figures.py"}
        path = STUDY / "figures" / f"{name}.{extension}"
        fig.savefig(path, dpi=600, bbox_inches="tight", pad_inches=0.12, metadata=metadata)
        print("Saved", path, flush=True)
    plt.close(fig)


def main():
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES.values()}
    price_plot.configure_style()
    diagnostics = price_plot.load_diagnostics()
    means, finance = load_results()
    save(price_plot.price_structure(diagnostics), "figure_1_price_structure",
         "Price structure in Poland and Lithuania, 2021-2025",
         "Annual means over complete UTC days. The six-hour band is the mean of the six most expensive "
         "and six cheapest hourly prices per day. Shading is not a confidence interval or storage profit.", hashes)
    save(technical_figure(means), "figure_2_technical_sensitivity",
         "Mean arbitrage margins under alternative storage durations and efficiencies",
         "Arithmetic means of independent 2023-2025 annual MILPs with continuous pumping power and binary "
         "pumping/generation exclusivity. Dark bars and dashed lines identify the 6 h, 75% baseline. "
         "Values are arbitrage margins, not supportable CAPEX; fixed O&M is not deducted.", hashes)
    save(financial_figure(finance), "figure_3_financial_results",
         "Financial implications of baseline arbitrage margins",
         "Baseline: 6 h, 75% efficiency. 2m is arbitrage plus equal supplementary NET contribution after "
         "service costs and displaced arbitrage, not doubled sales or forecast ancillary income. "
         "Dashed line: reference CAPEX EUR 2,500/kW. Remaining requirements refer to that CAPEX. "
         "At 7%, 40 years and fixed O&M of 2.2%, annual cost is approximately EUR 242.5/kW/year. "
         "Reported-EUR historical margins are held level; no common-price-year rebasing.", hashes)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[p.name] for p in SOURCES.values()):
        raise ValueError("Saved inputs changed during plotting")
    print("PASS: 40 price levels, 10 technical margins and 24 financial bar values match saved outputs.")


if __name__ == "__main__":
    main()
