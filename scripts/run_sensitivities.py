#!/usr/bin/env python3
"""Run the agreed continuous-power MILP sensitivities and screening valuation."""

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import run_phs as phs


BASE = phs.Parameters(pumping_formulation="continuous")
CONFIGURATIONS = {
    "base": BASE,
    "dur4": replace(BASE, duration_hours=4.0),
    "dur8": replace(BASE, duration_hours=8.0),
    "eta70": replace(BASE, efficiency=0.70),
    "eta80": replace(BASE, efficiency=0.80),
}
CAPEX_EUR_PER_KW = 2500.0
CAPEX_LEVELS_EUR_PER_KW = (2000.0, 2500.0, 3000.0)
FIXED_OM_FRACTION_PER_YEAR = 0.022
LIFETIME_YEARS = 40
DISCOUNT_RATES = (0.05, 0.07, 0.09)
BASE_DISCOUNT_RATE = 0.07
ALPHAS = (1, 2)
OUTPUTS = {name: phs.STUDY / "data" / filename for name, filename in {
    "dispatch": "phs_sensitivity_dispatch.parquet",
    "annual": "phs_sensitivity_annual.csv",
    "mean": "phs_sensitivity_mean.csv",
    "financial": "phs_financial_sensitivity.csv",
}.items()}


def crf(rate, years=LIFETIME_YEARS):
    if not math.isfinite(rate) or rate <= 0 or years <= 0:
        raise ValueError("Require a positive discount rate and lifetime")
    return rate / (1 - (1 + rate) ** (-years))


def financial_case(margin, rate, alpha, capex=CAPEX_EUR_PER_KW):
    if not math.isfinite(capex) or capex <= 0:
        raise ValueError("Require positive finite reference CAPEX")
    factor = crf(rate) + FIXED_OM_FRACTION_PER_YEAR
    contribution = alpha * margin
    cost = capex * factor
    supportable = contribution / factor
    if not math.isclose(contribution, supportable * factor, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Supportable CAPEX cost-recovery identity failed")
    return {
        "discount_rate": rate, "alpha": alpha, "lifetime_years": LIFETIME_YEARS,
        "reference_capex_eur_per_kw": capex,
        "fixed_om_fraction_per_year": FIXED_OM_FRACTION_PER_YEAR, "crf": crf(rate),
        "mean_margin_eur_per_kw_year": margin,
        "annual_contribution_eur_per_kw_year": contribution,
        "annual_cost_requirement_eur_per_kw_year": cost,
        "supportable_capex_eur_per_kw": supportable,
        "annual_surplus_eur_per_kw_year": contribution - cost,
        "remaining_net_revenue_requirement_eur_per_kw_year": max(0.0, cost - contribution),
    }


def financial_checks():
    required = financial_case(100.0, BASE_DISCOUNT_RATE, 1)["annual_cost_requirement_eur_per_kw_year"]
    if not math.isclose(required, 242.52285, rel_tol=0, abs_tol=1e-5):
        raise ValueError(f"Reference annual cost requirement failed: {required}")
    for margin in (0.0, 100.0, 300.0):
        for rate in DISCOUNT_RATES:
            one, two = (financial_case(margin, rate, a) for a in ALPHAS)
            if two["supportable_capex_eur_per_kw"] != 2 * one["supportable_capex_eur_per_kw"]:
                raise ValueError("Alpha scaling failed")
        if margin > 0:
            values = [financial_case(margin, r, 1)["supportable_capex_eur_per_kw"] for r in DISCOUNT_RATES]
            if not all(a > b for a, b in zip(values, values[1:])):
                raise ValueError("Discount-rate ordering failed")
        for capex in CAPEX_LEVELS_EUR_PER_KW:
            reference = financial_case(margin, BASE_DISCOUNT_RATE, 1)
            varied = financial_case(margin, BASE_DISCOUNT_RATE, 1, capex)
            if varied["supportable_capex_eur_per_kw"] != reference["supportable_capex_eur_per_kw"]:
                raise ValueError("Supportable CAPEX must not depend on assumed construction CAPEX")
            if not math.isclose(varied["annual_cost_requirement_eur_per_kw_year"],
                                reference["annual_cost_requirement_eur_per_kw_year"] * capex / CAPEX_EUR_PER_KW,
                                rel_tol=0, abs_tol=1e-9):
                raise ValueError("CAPEX/proportional-O&M scaling failed")
    print(f"PASS financial checks: reference annual requirement EUR {required:.8f}/kW/year", flush=True)
    return {"reference_annual_cost_requirement_eur_per_kw_year": required}


def aggregate(annual):
    means = []
    for country in phs.COUNTRIES:
        for config, p in CONFIGURATIONS.items():
            rows = [r for r in annual if (r["country"], r["configuration_id"]) == (country, config)]
            if {int(r["year"]) for r in rows} != set(phs.STUDY_YEARS) or len(rows) != len(phs.STUDY_YEARS):
                raise ValueError("Incomplete/duplicate country-configuration years")
            totals = {k: math.fsum(r[k] for r in rows) for k in phs.METRICS}
            if totals["generated_mwh"] <= 0 or totals["pumped_mwh"] <= 0:
                raise ValueError("Weighted prices undefined without positive energy throughput")
            buy = totals["pumping_expenditure_eur"] / totals["pumped_mwh"]
            sell = totals["sales_revenue_eur"] / totals["generated_mwh"]
            unit_margin = totals["arbitrage_margin_eur"] / totals["generated_mwh"]
            if not math.isclose(unit_margin, sell - buy / p.efficiency, rel_tol=0, abs_tol=1e-8):
                raise ValueError("Weighted price/margin identity failed")
            mean_margin = totals["margin_eur_per_kw_year"] / len(rows)
            means.append({
                "country": country, "configuration_id": config, **p.metadata(), "years": len(rows),
                **{f"mean_{k}": v / len(rows) for k, v in totals.items()},
                "mean_margin_upper_bound_eur_per_kw_year": math.fsum(r["objective_upper_bound_eur"] for r in rows)
                    / (len(rows) * p.generating_power_mw * 1000),
                "weighted_buy_price_eur_mwh": buy, "weighted_sell_price_eur_mwh": sell,
                "margin_per_generated_mwh_eur": unit_margin,
                "supportable_capex_7pct_arbitrage_eur_per_kw": financial_case(mean_margin, BASE_DISCOUNT_RATE, 1)
                    ["supportable_capex_eur_per_kw"],
            })
    for row in means:
        baseline = next(r for r in means if r["country"] == row["country"] and r["configuration_id"] == "base")
        margin, base = row["mean_margin_eur_per_kw_year"], baseline["mean_margin_eur_per_kw_year"]
        row["margin_change_eur_per_kw_year"] = margin - base
        row["margin_change_pct"] = 100 * (margin - base) / base
        row["margin_change_lower_bound_eur_per_kw_year"] = (
            0.0 if row is baseline else margin - baseline["mean_margin_upper_bound_eur_per_kw_year"])
        row["margin_change_upper_bound_eur_per_kw_year"] = (
            0.0 if row is baseline else row["mean_margin_upper_bound_eur_per_kw_year"] - base)
    return means


def aggregation_checks():
    fixtures = []
    for country in phs.COUNTRIES:
        for config, p in CONFIGURATIONS.items():
            for year, pumped, buy, sell in zip(phs.STUDY_YEARS, (10, 20, 30), (-10, 20, 40), (100, 120, 140)):
                generated = pumped * p.efficiency
                margin = sell * generated - buy * pumped
                fixtures.append({"country": country, "configuration_id": config, "year": year,
                    "pumped_mwh": pumped, "generated_mwh": generated,
                    "pumping_expenditure_eur": buy * pumped, "sales_revenue_eur": sell * generated,
                    "arbitrage_margin_eur": margin, "margin_eur_per_kw_year": margin / (p.generating_power_mw * 1000),
                    "equivalent_full_discharge_cycles": generated / p.energy_capacity_mwh,
                    "objective_upper_bound_eur": margin})
    rows = aggregate(fixtures)
    for row in rows:
        if not (math.isclose(row["weighted_buy_price_eur_mwh"], 25.0, abs_tol=1e-10)
                and math.isclose(row["weighted_sell_price_eur_mwh"], 380.0 / 3, abs_tol=1e-10)):
            raise ValueError("Signed, unequal-energy weighted-price fixture failed")
    if len(financial_results(rows)) != 12:
        raise ValueError("Financial fixture row count failed")
    print("PASS aggregation fixture: ratios of totals, including negative pumping expenditure", flush=True)


def incremental_values(annual, means):
    increments = []
    for country in phs.COUNTRIES:
        for low, high in (("dur4", "base"), ("base", "dur8"), ("eta70", "base"), ("base", "eta80")):
            a, b = [next(r for r in means if (r["country"], r["configuration_id"]) == (country, c))
                    for c in (low, high)]
            if (low, high) in (("dur4", "base"), ("base", "dur8")):
                for year in phs.STUDY_YEARS:
                    shorter, longer = [next(r for r in annual if (r["country"], r["year"], r["configuration_id"])
                                           == (country, year, c)) for c in (low, high)]
                    if longer["objective_upper_bound_eur"] < shorter["objective_eur"] - phs.ANNUAL_TOL:
                        raise ValueError("Duration monotonicity violated beyond solver uncertainty")
            delta = b["mean_margin_eur_per_kw_year"] - a["mean_margin_eur_per_kw_year"]
            factor = crf(BASE_DISCOUNT_RATE) + FIXED_OM_FRACTION_PER_YEAR
            increments.append({"country": country, "from": low, "to": high,
                "additional_margin_eur_per_kw_year": delta,
                "additional_supportable_capex_eur_per_kw": delta / factor,
                "margin_change_lower_bound": b["mean_margin_eur_per_kw_year"] - a["mean_margin_upper_bound_eur_per_kw_year"],
                "margin_change_upper_bound": b["mean_margin_upper_bound_eur_per_kw_year"] - a["mean_margin_eur_per_kw_year"]})
    return increments


def financial_results(means, capex=CAPEX_EUR_PER_KW):
    rows = []
    for m in means:
        if m["configuration_id"] != "base":
            continue
        for rate in DISCOUNT_RATES:
            for alpha in ALPHAS:
                r = dict(country=m["country"], configuration_id="base",
                         **financial_case(m["mean_margin_eur_per_kw_year"], rate, alpha, capex))
                upper = financial_case(m["mean_margin_upper_bound_eur_per_kw_year"], rate, alpha, capex)
                r["supportable_capex_upper_bound_eur_per_kw"] = upper["supportable_capex_eur_per_kw"]
                r["annual_surplus_upper_bound_eur_per_kw_year"] = upper["annual_surplus_eur_per_kw_year"]
                rows.append(r)
    for country in phs.COUNTRIES:
        for rate in DISCOUNT_RATES:
            one, two = [next(r for r in rows if (r["country"], r["discount_rate"], r["alpha"]) == (country, rate, a))
                        for a in ALPHAS]
            if two["supportable_capex_eur_per_kw"] != 2 * one["supportable_capex_eur_per_kw"]:
                raise ValueError("Actual financial alpha scaling failed")
        for alpha in ALPHAS:
            values = [r["supportable_capex_eur_per_kw"] for r in rows if r["country"] == country and r["alpha"] == alpha]
            if not all(a > b for a, b in zip(values, values[1:])):
                raise ValueError("Actual financial discount ordering failed")
    return rows


def assemble_baseline(reference, reference_audit):
    table = pq.read_table(phs.REFERENCE_DISPATCH)
    old_audit = json.loads(table.schema.metadata[b"phs_baseline_provenance"])
    summaries = []
    for r in reference:
        key = f"{r['country']}_{int(r['year'])}"
        summaries.append({**r, **BASE.metadata(), "configuration_id": "base", "result_origin": "cached",
            "objective_eur": r["arbitrage_margin_eur"], "solver_nodes": None,
            "solver_message": old_audit["solver_messages"][key],
            "input_sha256": reference_audit["input_sha256"], "model_sha256": old_audit["script_sha256"],
            "validation_json": json.dumps(reference_audit["validation"][key], sort_keys=True)})
    dispatch = [{**r, **BASE.metadata(), "configuration_id": "base", "result_origin": "cached",
                 "plant_mode": float(r["pumping_mw"] > phs.PHYSICAL_TOL),
                 "plant_mode_source": "reconstructed", "operating_pump_count": None}
                for r in table.to_pylist()]
    return dispatch, summaries, old_audit


def validate_combined(table, annual, prices, reference):
    expected = {(c, y, k) for c in phs.COUNTRIES for y in phs.STUDY_YEARS for k in CONFIGURATIONS}
    if len(annual) != 30 or {(r["country"], r["year"], r["configuration_id"]) for r in annual} != expected:
        raise ValueError("Expected exactly 30 unique annual cases")
    if set(table["configuration_id"].to_pylist()) != set(CONFIGURATIONS):
        raise ValueError("Unexpected dispatch configurations")
    checks = {}
    for config, p in CONFIGURATIONS.items():
        selected = table.filter(pc.equal(table["configuration_id"], config))
        rows = [r for r in annual if r["configuration_id"] == config]
        checks[config] = phs.validate_saved(selected, rows, prices, p)
        if config == "base":
            original = pq.read_table(phs.REFERENCE_DISPATCH)
            if not selected.select(original.column_names).replace_schema_metadata(None).equals(original.replace_schema_metadata(None)):
                raise ValueError("Cached baseline dispatch differs from original")
            for row in rows:
                old = next(r for r in reference if (r["country"], r["year"]) == (row["country"], row["year"]))
                if any(row[k] != v for k, v in old.items()):
                    raise ValueError("Cached baseline summary differs from original")
    return checks


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with path.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    if len(saved) != len(rows):
        raise ValueError("CSV row-count readback failed")
    readback = []
    for saved_row, row in zip(saved, rows):
        values = {k: None if v is None else saved_row[k] if isinstance(v, str) else float(saved_row[k])
                  for k, v in row.items()}
        if values != row:
            raise ValueError(f"CSV round-trip failed: {path}")
        readback.append(values)
    return readback


def capex_sensitivity():
    """Extend saved baseline finances only; never invoke the dispatch solver."""
    output = phs.STUDY / "data/phs_capex_sensitivity.csv"
    if output.exists():
        raise FileExistsError("CAPEX sensitivity output already exists; refusing to overwrite")
    financial_checks()
    hashes = {str(OUTPUTS[k]): hashlib.sha256(OUTPUTS[k].read_bytes()).hexdigest()
              for k in ("mean", "financial")}

    def read_numeric_csv(path):
        with path.open(newline="", encoding="utf-8") as handle:
            return [{k: v if k in ("country", "configuration_id", "pumping_formulation") else float(v)
                     for k, v in row.items()} for row in csv.DictReader(handle)]

    means = read_numeric_csv(OUTPUTS["mean"])
    original = read_numeric_csv(OUTPUTS["financial"])
    if len(means) != 10 or len({(r["country"], r["configuration_id"]) for r in means}) != 10:
        raise ValueError("Expected 10 saved technical mean rows")
    baseline = [r for r in means if r["configuration_id"] == "base"]
    if len(baseline) != len(phs.COUNTRIES) or {r["country"] for r in baseline} != set(phs.COUNTRIES):
        raise ValueError("Expected one baseline mean per country")
    for row in baseline:
        if any(row[k] != v for k, v in BASE.metadata().items()) or row["years"] != len(phs.STUDY_YEARS):
            raise ValueError("Saved baseline parameters do not match")
    if financial_results(baseline) != original:
        raise ValueError("Saved baseline financial results do not reconcile exactly")
    timestamp = datetime.now(timezone.utc).isoformat()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rows = []
    for capex in CAPEX_LEVELS_EUR_PER_KW:
        for row in financial_results(baseline, capex):
            row.update(fixed_om_eur_per_kw_year=capex * FIXED_OM_FRACTION_PER_YEAR,
                       mean_source_sha256=hashes[str(OUTPUTS["mean"])],
                       financial_source_sha256=hashes[str(OUTPUTS["financial"])],
                       script_sha256=script_hash, run_timestamp_utc=timestamp)
            rows.append(row)
    expected = {(c, r, a, cap) for c in phs.COUNTRIES for r in DISCOUNT_RATES
                for a in ALPHAS for cap in CAPEX_LEVELS_EUR_PER_KW}
    if len(rows) != len(expected) or {(r["country"], r["discount_rate"], r["alpha"],
                                     r["reference_capex_eur_per_kw"]) for r in rows} != expected:
        raise ValueError("CAPEX sensitivity keys are missing or duplicated")
    for country in phs.COUNTRIES:
        for rate in DISCOUNT_RATES:
            for alpha in ALPHAS:
                selected = [r for r in rows if (r["country"], r["discount_rate"], r["alpha"]) == (country, rate, alpha)]
                if len({r["supportable_capex_eur_per_kw"] for r in selected}) != 1:
                    raise ValueError("Supportable CAPEX varies with assumed cost")
                if not all(a["annual_surplus_eur_per_kw_year"] > b["annual_surplus_eur_per_kw_year"]
                           for a, b in zip(selected, selected[1:])):
                    raise ValueError("Higher CAPEX must reduce the annual surplus")
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in hashes.items()):
        raise ValueError("Saved inputs changed during CAPEX postprocessing")
    saved = write_csv(output, rows)
    print(f"PASS CAPEX postprocessing: {len(saved)} unique rows, exact EUR 2500 reconciliation, readback passed")
    for r in saved:
        print(f"{r['country']} r={r['discount_rate']:.0%} alpha={r['alpha']:.0f} "
              f"CAPEX={r['reference_capex_eur_per_kw']:.0f}: "
              f"surplus={r['annual_surplus_eur_per_kw_year']:+.4f}, "
              f"remaining={r['remaining_net_revenue_requirement_eur_per_kw_year']:.4f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Validate model, finances and cache without annual solves")
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--solver-log", action="store_true")
    parser.add_argument("--capex-only", action="store_true", help="Postprocess saved baseline means at three CAPEX levels; no MILPs")
    args = parser.parse_args()
    if args.capex_only:
        if args.self_test:
            parser.error("--capex-only cannot be combined with --self-test")
        capex_sensitivity()
        return
    tests, finance_checks = phs.small_checks(), financial_checks()
    aggregation_checks()
    source_hash = hashlib.sha256(phs.INPUT.read_bytes()).hexdigest()
    model_hash = hashlib.sha256(Path(phs.__file__).read_bytes()).hexdigest()
    prices = phs.read_annual_prices(phs.STUDY_YEARS, BASE)
    reference, reference_audit = phs.load_reference(prices, BASE, source_hash)
    dispatch, annual, original_audit = assemble_baseline(reference, reference_audit)
    baseline_table = pa.Table.from_pylist(dispatch)
    phs.validate_saved(baseline_table, annual, prices, BASE)
    if args.self_test:
        print("PASS sensitivity preparation: cached baseline assembled and validated; no annual solves")
        return
    if any(p.exists() for p in OUTPUTS.values()):
        raise FileExistsError("Sensitivity outputs already exist; refusing to overwrite")
    audit = {"run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": str(phs.INPUT), "input_sha256": source_hash,
        "model_sha256": model_hash, "orchestration_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "solver": "HiGHS via scipy.optimize.milp", "scipy_version": phs.scipy.__version__,
        "numpy_version": np.__version__, "pyarrow_version": pa.__version__,
        "mip_rel_gap_target": phs.MIP_GAP, "time_limit_seconds_per_case": args.time_limit,
        "configurations": {k: p.metadata() for k, p in CONFIGURATIONS.items()},
        "study_years": list(phs.STUDY_YEARS), "small_checks": tests, "financial_checks": finance_checks,
        "reference_validation": reference_audit, "original_baseline_provenance": original_audit,
        "cached_objective_note": "Historical incumbent reconstructed from saved cash flows; original summaries unchanged",
        "energy_convention": "discharge-equivalent MWh",
        "financial_assumptions": {"capex_eur_per_kw": CAPEX_EUR_PER_KW, "fixed_om_fraction_per_year": FIXED_OM_FRACTION_PER_YEAR,
            "lifetime_years": LIFETIME_YEARS, "discount_rates": DISCOUNT_RATES, "alphas": ALPHAS,
            "price_convention": "Reported EUR, no common-price-year rebasing; historical mean held level annually",
            "supplementary_contribution": "Equal net contribution after additional service costs and displaced arbitrage"}}
    for config, p in CONFIGURATIONS.items():
        if config == "base":
            continue
        for (country, year), (timestamps, hourly_prices) in prices.items():
            print(f"Solving {config} {country} {year}: {len(timestamps)} hours", flush=True)
            result = phs.solve_dispatch(hourly_prices, p, time_limit=args.time_limit, solver_log=args.solver_log)
            annual.append({"country": country, "year": year, **p.metadata(),
                **{k: result[k] for k in phs.METRICS}, "configuration_id": config, "result_origin": "new",
                **{k: result[k] for k in ("objective_eur", "solver_status", "solver_status_code", "solver_message",
                    "objective_upper_bound_eur", "optimality_gap", "solver_seconds", "solver_nodes")},
                "hours": len(timestamps), "solver": f"HiGHS (SciPy {phs.scipy.__version__})",
                "input_sha256": source_hash, "model_sha256": model_hash,
                "validation_json": json.dumps(result["validation"], sort_keys=True)})
            dispatch.extend({"country": country, "timestamp_utc": timestamp, "price_eur_mwh": float(hourly_prices[t]),
                "pumping_mw": float(result["pump"][t]), "generation_mw": float(result["generate"][t]),
                "energy_begin_mwh": float(result["energy"][t]), "energy_end_mwh": float(result["energy"][t + 1]),
                "plant_mode": float(result["mode"][t]), "plant_mode_source": "solver", "operating_pump_count": None,
                **p.metadata(), "configuration_id": config, "result_origin": "new"}
                for t, timestamp in enumerate(timestamps))
            print(f"PASS {config} {country} {year}: margin={result['margin_eur_per_kw_year']:.6f}, "
                  f"gap={result['optimality_gap']:.8g}, seconds={result['solver_seconds']:.2f}", flush=True)
    means = aggregate(annual)
    financial = financial_results(means)
    if len(means) != 10 or len(financial) != 12:
        raise ValueError("Expected 10 technical means and 12 baseline financial comparisons")
    audit["incremental_values"] = incremental_values(annual, means)
    table = pa.Table.from_pylist(dispatch)
    audit["validation"] = validate_combined(table, annual, prices, reference)
    protected = {str(phs.INPUT): source_hash, str(Path(phs.__file__)): model_hash,
                 **reference_audit["reference_files_sha256"]}
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in protected.items()):
        raise ValueError("Input, model or cached reference changed during batch")
    table = table.replace_schema_metadata({b"phs_sensitivity_provenance": json.dumps(audit, sort_keys=True).encode()})
    pq.write_table(table, OUTPUTS["dispatch"], compression="snappy")
    saved = pq.read_table(OUTPUTS["dispatch"])
    if not saved.equals(table, check_metadata=True):
        raise ValueError("Dispatch Parquet round-trip failed")
    annual_readback = write_csv(OUTPUTS["annual"], annual)
    mean_readback = write_csv(OUTPUTS["mean"], means)
    finance_readback = write_csv(OUTPUTS["financial"], financial)
    validate_combined(saved, annual_readback, prices, reference)
    if aggregate(annual_readback) != mean_readback or financial_results(mean_readback) != finance_readback:
        raise ValueError("Saved aggregation/financial reconciliation failed")
    print(f"PASS readback: {len(annual)} annual, {len(means)} mean, {len(financial)} financial rows; {table.num_rows} dispatch rows")
    for row in means:
        print("MEAN", json.dumps(row))
    for row in audit["incremental_values"]:
        print("INCREMENT", json.dumps(row))
    for row in financial:
        print("FINANCIAL", json.dumps(row))


if __name__ == "__main__":
    main()
