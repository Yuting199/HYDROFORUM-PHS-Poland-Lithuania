#!/usr/bin/env python3
"""Validate continuous/discrete pumping and compare independent annual MILPs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


STUDY = Path(__file__).resolve().parents[1]
INPUT = STUDY / "data/prices_PL_LT_hourly_2021_2025.parquet"
COUNTRIES = ("PL", "LT")
STUDY_YEARS = (2023, 2024, 2025)
MIP_GAP = 1e-4
PHYSICAL_TOL = 1e-6
ANNUAL_TOL = 1e-5
REFERENCE_DISPATCH = STUDY / "data/phs_baseline_dispatch.parquet"
REFERENCE_ANNUAL = STUDY / "data/phs_baseline_annual.csv"


@dataclass(frozen=True)
class Parameters:
    generating_power_mw: float = 1.0
    pumping_power_mw: float = 1.0
    duration_hours: float = 6.0
    efficiency: float = 0.75
    initial_soc_fraction: float = 0.5
    pumping_formulation: str = "continuous"
    n_pump_units: int = 4
    dt_hours: float = 1.0

    def __post_init__(self):
        for value in (self.generating_power_mw, self.pumping_power_mw,
                      self.duration_hours, self.dt_hours):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Power, duration and time step must be finite and positive")
        if not 0 < self.efficiency <= 1 or not 0 <= self.initial_soc_fraction <= 1:
            raise ValueError("Require efficiency in (0, 1] and SOC fraction in [0, 1]")
        if self.pumping_formulation not in ("continuous", "discrete"):
            raise ValueError("Unknown pumping formulation")
        if isinstance(self.n_pump_units, bool) or not isinstance(self.n_pump_units, int) or self.n_pump_units < 1:
            raise ValueError("Number of pumping units must be a positive integer")

    @property
    def energy_capacity_mwh(self):
        return self.generating_power_mw * self.duration_hours

    @property
    def initial_energy_mwh(self):
        return self.initial_soc_fraction * self.energy_capacity_mwh

    @property
    def unit_pumping_power_mw(self):
        return self.pumping_power_mw / self.n_pump_units

    def metadata(self):
        return dict(asdict(self), energy_capacity_mwh=self.energy_capacity_mwh,
                    initial_and_terminal_energy_mwh=self.initial_energy_mwh,
                    unit_pumping_power_mw=self.unit_pumping_power_mw)


def solve_dispatch(prices, parameters=Parameters(), initial_energy=None,
                   relative_gap=MIP_GAP, time_limit=300.0, solver_log=False):
    """Return grid-side powers and all T+1 discharge-equivalent energy states.

    Initial and terminal energy are identical. The explicit override is for tests.
    All pumps share one reservoir; generation remains continuous in both modes.
    """
    prices = np.asarray(prices, dtype=float)
    if prices.ndim != 1 or not prices.size or not np.isfinite(prices).all():
        raise ValueError("Prices must be a nonempty finite one-dimensional series")
    p = parameters
    capacity = p.energy_capacity_mwh
    initial = p.initial_energy_mwh if initial_energy is None else float(initial_energy)
    if not 0 <= initial <= capacity:
        raise ValueError("Initial/terminal energy must be within storage capacity")
    if not 0 < relative_gap <= MIP_GAP or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("Require 0 < relative_gap <= 1e-4 and a finite positive time limit")

    n = len(prices)
    t = np.arange(n)
    # Variable blocks: pump[T], generate[T], E[T+1], mode[T], optional pump count[T].
    pump, generate, energy, mode = t, n + t, 2 * n + np.arange(n + 1), 3 * n + 1 + t
    discrete = p.pumping_formulation == "discrete"
    count = 4 * n + 1 + t if discrete else None
    size = (5 if discrete else 4) * n + 1
    objective = np.zeros(size)
    objective[pump], objective[generate] = prices * p.dt_hours, -prices * p.dt_hours
    lower, upper = np.zeros(size), np.zeros(size)
    upper[pump], upper[generate] = p.pumping_power_mw, p.generating_power_mw
    upper[energy] = capacity
    upper[mode] = 1
    lower[energy[0]] = upper[energy[0]] = initial
    lower[energy[-1]] = upper[energy[-1]] = initial
    integrality = np.zeros(size, dtype=np.uint8)
    integrality[mode] = 1
    if discrete:
        upper[count] = p.n_pump_units
        integrality[count] = 1

    row = np.concatenate([t, t, t, t, n + t, n + t, 2 * n + t, 2 * n + t])
    col = np.concatenate([pump, generate, energy[:-1], energy[1:],
                          count if discrete else pump, mode, generate, mode])
    values = np.concatenate([
        np.full(n, -p.efficiency * p.dt_hours), np.full(n, p.dt_hours),
        -np.ones(n), np.ones(n), np.ones(n),
        np.full(n, -p.n_pump_units if discrete else -p.pumping_power_mw),
        np.ones(n), np.full(n, p.generating_power_mw),
    ])
    constraint_lower = np.concatenate([np.zeros(n), np.full(2 * n, -np.inf)])
    constraint_upper = np.concatenate([np.zeros(2 * n), np.full(n, p.generating_power_mw)])
    if discrete:
        row = np.concatenate([row, 3 * n + t, 3 * n + t])
        col = np.concatenate([col, pump, count])
        values = np.concatenate([values, np.ones(n), np.full(n, -p.unit_pumping_power_mw)])
        constraint_lower = np.concatenate([constraint_lower, np.zeros(n)])
        constraint_upper = np.concatenate([constraint_upper, np.zeros(n)])
    matrix = coo_matrix((values, (row, col)), shape=(len(constraint_lower), size)).tocsc()
    started = time.perf_counter()
    result = milp(
        objective, integrality=integrality, bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, constraint_lower, constraint_upper),
        options={"mip_rel_gap": relative_gap, "time_limit": time_limit,
                 "presolve": True, "disp": solver_log},
    )
    elapsed = time.perf_counter() - started
    gap = getattr(result, "mip_gap", None)
    if result.status != 0 or not result.success or result.x is None:
        raise RuntimeError(f"Uncompleted MILP: status={result.status}, gap={gap}, "
                           f"minimisation_incumbent={getattr(result, 'fun', None)}, "
                           f"minimisation_bound={getattr(result, 'mip_dual_bound', None)}, "
                           f"seconds={elapsed:.2f}, message={result.message}")
    if gap is None or not math.isfinite(gap) or gap > relative_gap + 1e-12:
        raise RuntimeError(f"MILP returned an unacceptable optimality gap: {gap}")
    solution = {
        "pump": result.x[pump], "generate": result.x[generate],
        "energy": result.x[energy], "mode": result.x[mode],
        "pump_count": result.x[count] if discrete else None,
        "objective_eur": -float(result.fun), "solver_status": "optimal",
        "solver_status_code": int(result.status), "solver_message": str(result.message),
        "optimality_gap": float(gap), "objective_upper_bound_eur": -float(result.mip_dual_bound),
        "solver_seconds": elapsed, "solver_nodes": int(result.mip_node_count),
    }
    solution["validation"] = validate_dispatch(prices, solution, p, initial)
    return solution


def validate_dispatch(prices, solution, parameters, initial=None):
    p = parameters
    capacity = p.energy_capacity_mwh
    initial = p.initial_energy_mwh if initial is None else initial
    pump, generate, energy, mode = (solution[k] for k in ("pump", "generate", "energy", "mode"))
    if any(not np.isfinite(v).all() for v in (pump, generate, energy, mode)):
        raise ValueError("Nonfinite dispatch solution")
    if len(energy) != len(prices) + 1 or any(len(v) != len(prices) for v in (pump, generate, mode)):
        raise ValueError("Expected T powers/modes and T+1 energy states")
    checks = {
        "power_bound_violation_mw": max(0.0, -min(pump.min(), generate.min()),
                                         pump.max() - p.pumping_power_mw,
                                         generate.max() - p.generating_power_mw),
        "energy_bound_violation_mwh": max(0.0, -energy.min(), energy.max() - capacity),
        "mode_bound_violation": max(0.0, -mode.min(), mode.max() - 1),
        "mode_integrality_error": float(np.max(np.abs(mode - np.rint(mode)))),
        "mode_constraint_violation_mw": max(0.0, float(np.max(pump - p.pumping_power_mw * mode)),
                                             float(np.max(generate - p.generating_power_mw * (1 - mode)))),
        "simultaneous_power_mw": float(np.max(np.minimum(pump, generate))),
        "energy_balance_error_mwh": float(np.max(np.abs(
            np.diff(energy) - p.efficiency * pump * p.dt_hours + generate * p.dt_hours))),
        "initial_energy_error_mwh": abs(float(energy[0]) - initial),
        "terminal_energy_error_mwh": abs(float(energy[-1]) - initial),
    }
    if p.pumping_formulation == "discrete":
        count = solution["pump_count"]
        if count is None or len(count) != len(prices) or not np.isfinite(count).all():
            raise ValueError("Missing/nonfinite operating pump counts")
        checks.update(
            pump_count_bound_violation=max(0.0, -count.min(), count.max() - p.n_pump_units),
            pump_count_integrality_error=float(np.max(np.abs(count - np.rint(count)))),
            pump_count_power_error_mw=float(np.max(np.abs(pump - p.unit_pumping_power_mw * count))),
            pump_count_mode_violation=max(0.0, float(np.max(count - p.n_pump_units * mode))),
        )
    for label, value in checks.items():
        if value > PHYSICAL_TOL:
            raise ValueError(f"Dispatch validation failed: {label}={value}")
    pumped = math.fsum(float(v) * p.dt_hours for v in pump)
    generated = math.fsum(float(v) * p.dt_hours for v in generate)
    sales = math.fsum(float(price) * float(g) * p.dt_hours for price, g in zip(prices, generate))
    expenditure = math.fsum(float(price) * float(c) * p.dt_hours for price, c in zip(prices, pump))
    checks["annual_energy_error_mwh"] = abs(generated - p.efficiency * pumped)
    checks["objective_reconstruction_error_eur"] = abs(sales - expenditure - solution["objective_eur"])
    if checks["annual_energy_error_mwh"] > ANNUAL_TOL:
        raise ValueError(f"Annual energy conservation failed: {checks}")
    if checks["objective_reconstruction_error_eur"] > ANNUAL_TOL:
        raise ValueError(f"Cash-flow reconstruction failed: {checks}")
    solution.update(pumped_mwh=pumped, generated_mwh=generated,
                    sales_revenue_eur=sales, pumping_expenditure_eur=expenditure,
                    arbitrage_margin_eur=sales - expenditure,
                    margin_eur_per_kw_year=(sales - expenditure) / (p.generating_power_mw * 1000),
                    equivalent_full_discharge_cycles=generated / capacity)
    return checks


def small_checks():
    results = {}
    cases = [
        ("constant_positive", [50.0] * 24, 6.0, 3.0, (0.0, 0.0)),
        ("two_hour_positive", [10.0, 100.0], 1.0, 0.0, (65.0, 65.0)),
        ("two_hour_negative", [-100.0, -100.0], 1.0, 0.0, (25.0, 25.0)),
        ("binding_discreteness", [10.0, 100.0], 0.5, 0.0, (130.0 / 3, 32.5)),
    ]
    for index, formulation in enumerate(("continuous", "discrete")):
        for name, prices, duration, initial, expected in cases:
            p = Parameters(duration_hours=duration, pumping_formulation=formulation)
            result = solve_dispatch(prices, p, initial, relative_gap=1e-9, time_limit=30)
            if abs(result["arbitrage_margin_eur"] - expected[index]) > PHYSICAL_TOL:
                raise ValueError(f"{name}/{formulation}: unexpected margin")
            results[f"{name}_{formulation}"] = {
                "expected_margin_eur": expected[index], "margin_eur": result["arbitrage_margin_eur"],
                "optimality_gap": result["optimality_gap"], "validation": result["validation"]}
            print(f"PASS {name}/{formulation}: EUR {result['arbitrage_margin_eur']:.8f}", flush=True)
        # Non-default SOC, duration, ratings and dt expose normalization/boundary hardcoding.
        p = Parameters(duration_hours=0.5, initial_soc_fraction=0.25,
                       pumping_formulation=formulation, dt_hours=0.5)
        doubled = replace(p, generating_power_mw=2 * p.generating_power_mw,
                          pumping_power_mw=2 * p.pumping_power_mw)
        base = solve_dispatch([10, 100, 10, 100], p, relative_gap=1e-9, time_limit=30)
        scaled = solve_dispatch([10, 100, 10, 100], doubled, relative_gap=1e-9, time_limit=30)
        checks = {
            "capacity_scaling": doubled.energy_capacity_mwh - 2 * p.energy_capacity_mwh,
            "boundary_scaling": scaled["energy"][0] - 2 * base["energy"][0],
            "margin_scaling": scaled["arbitrage_margin_eur"] - 2 * base["arbitrage_margin_eur"],
            "per_kw_invariance": scaled["margin_eur_per_kw_year"] - base["margin_eur_per_kw_year"],
            "cycle_invariance": scaled["equivalent_full_discharge_cycles"] - base["equivalent_full_discharge_cycles"],
            "cycle_denominator": scaled["equivalent_full_discharge_cycles"] * doubled.energy_capacity_mwh
                                 - scaled["generated_mwh"],
            "per_kw_denominator": scaled["margin_eur_per_kw_year"] * doubled.generating_power_mw * 1000
                                  - scaled["arbitrage_margin_eur"],
        }
        if base["arbitrage_margin_eur"] <= 0 or any(abs(v) > PHYSICAL_TOL for v in checks.values()):
            raise ValueError(f"Power-scaling test failed: {checks}")
        results[f"power_scaling_{formulation}"] = checks
        print(f"PASS power_scaling/{formulation}: EUR {base['arbitrage_margin_eur']:.8f} -> "
              f"{scaled['arbitrage_margin_eur']:.8f}; equal per-kW margins and cycles", flush=True)
    return results


def read_annual_prices(years=STUDY_YEARS, parameters=Parameters()):
    if not years or len(set(years)) != len(years):
        raise ValueError("Select nonempty, distinct study years")
    if parameters.dt_hours != 1.0:
        raise ValueError("The annual source is hourly; other dt values are only supported in small tests")
    table = pq.read_table(INPUT, columns=["timestamp_utc", "PL_EUR_MWh", "LT_EUR_MWh"])
    if table.schema.field("timestamp_utc").type.tz != "UTC":
        raise ValueError("Input timestamps must be explicitly UTC")
    rows = table.to_pylist()
    annual = {}
    for year in years:
        selected = [r for r in rows if r["timestamp_utc"].year == year]
        first = datetime(year, 1, 1, tzinfo=timezone.utc)
        n = int((datetime(year + 1, 1, 1, tzinfo=timezone.utc) - first).total_seconds() / 3600)
        if len(selected) != n or any(r["timestamp_utc"] != first + timedelta(hours=i)
                                    for i, r in enumerate(selected)):
            raise ValueError(f"Incomplete or unordered UTC year: {year}")
        for country in COUNTRIES:
            prices = np.asarray([r[f"{country}_EUR_MWh"] for r in selected], dtype=float)
            if not np.isfinite(prices).all():
                raise ValueError(f"Missing/nonfinite input prices: {country} {year}")
            annual[(country, year)] = ([r["timestamp_utc"] for r in selected], prices)
    return annual


METRICS = ("pumped_mwh", "generated_mwh", "sales_revenue_eur", "pumping_expenditure_eur",
           "arbitrage_margin_eur", "margin_eur_per_kw_year", "equivalent_full_discharge_cycles")


def read_summaries(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return [{k: v if k in ("country", "solver", "solver_status", "pumping_formulation")
                 else float(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def validate_saved(table, summaries, annual_prices, parameters, legacy=False):
    """Validate saved unrounded dispatch, temporal continuity, summaries and bounds."""
    expected_keys = set(annual_prices)
    if len(summaries) != len(expected_keys) or {(r["country"], int(r["year"])) for r in summaries} != expected_keys:
        raise ValueError("Unexpected or duplicate annual summary rows")
    groups = {key: [] for key in expected_keys}
    for row in table.to_pylist():
        key = row["country"], row["timestamp_utc"].year
        if key not in groups:
            raise ValueError(f"Unexpected dispatch country/year: {key}")
        groups[key].append(row)
    audits = {}
    for summary in summaries:
        key = summary["country"], int(summary["year"])
        rows = groups[key]
        timestamps, prices = annual_prices[key]
        if [r["timestamp_utc"] for r in rows] != timestamps:
            raise ValueError(f"Saved dispatch timestamp mismatch: {key}")
        if not np.array_equal([r["price_eur_mwh"] for r in rows], prices):
            raise ValueError(f"Saved dispatch price mismatch: {key}")
        begin, end = (np.array([r[k] for r in rows]) for k in ("energy_begin_mwh", "energy_end_mwh"))
        if np.max(np.abs(begin[1:] - end[:-1])) > PHYSICAL_TOL:
            raise ValueError(f"Saved energy state continuity failed: {key}")
        pump = np.array([r["pumping_mw"] for r in rows])
        solution = {
            "pump": pump, "generate": np.array([r["generation_mw"] for r in rows]),
            "energy": np.concatenate([begin[:1], end]),
            # Historical files did not save u; reconstruct a feasible binary mode.
            "mode": (pump > PHYSICAL_TOL).astype(float) if legacy else np.array([r["plant_mode"] for r in rows]),
            "pump_count": None if legacy or parameters.pumping_formulation == "continuous"
                          else np.array([r["operating_pump_count"] for r in rows]),
            "objective_eur": summary.get("objective_eur", summary["arbitrage_margin_eur"]),
        }
        audits[f"{key[0]}_{key[1]}"] = validate_dispatch(prices, solution, parameters)
        for field in METRICS:
            if not math.isclose(solution[field], summary[field], rel_tol=0, abs_tol=ANNUAL_TOL):
                raise ValueError(f"Saved {field} mismatch: {key}")
        for field in ("duration_hours", "efficiency"):
            if summary[field] != getattr(parameters, field):
                raise ValueError(f"Saved parameter mismatch: {field}")
        if not legacy:
            for field, expected in parameters.metadata().items():
                if summary[field] != expected or any(r[field] != expected for r in rows):
                    raise ValueError(f"Saved parameter mismatch: {field}")
        objective, bound, gap = solution["objective_eur"], summary["objective_upper_bound_eur"], summary["optimality_gap"]
        if (summary["hours"] != len(prices) or summary["solver_status"] != "optimal"
                or summary["solver_status_code"] != 0 or not np.isfinite([objective, bound, gap, summary["solver_seconds"]]).all()
                or bound < objective - ANNUAL_TOL or not 0 <= gap <= MIP_GAP + 1e-12):
            raise ValueError(f"Invalid saved solver outcome: {key}")
        expected_gap = abs(bound - objective) / max(abs(objective), 1e-10)
        if not math.isclose(expected_gap, gap, rel_tol=1e-6, abs_tol=1e-10):
            raise ValueError(f"Saved objective bound/gap mismatch: {key}")
    return audits


def load_reference(annual_prices, parameters, source_hash):
    table = pq.read_table(REFERENCE_DISPATCH)
    audit = json.loads(table.schema.metadata[b"phs_baseline_provenance"])
    expected = {"duration_hours": parameters.duration_hours, "efficiency": parameters.efficiency,
                "dt_hours": parameters.dt_hours, "power_mw": parameters.generating_power_mw,
                "initial_and_terminal_energy_mwh": parameters.initial_energy_mwh,
                "input_sha256": source_hash, "energy_convention": "discharge-equivalent MWh"}
    if parameters.pumping_power_mw != parameters.generating_power_mw or any(audit.get(k) != v for k, v in expected.items()):
        raise ValueError("Continuous reference provenance/technical mismatch; do not use for this comparison")
    summaries = [r for r in read_summaries(REFERENCE_ANNUAL)
                 if (r["country"], int(r["year"])) in annual_prices]
    table = table.filter(pa.array([(r["country"], r["timestamp_utc"].year) in annual_prices
                                   for r in table.select(["country", "timestamp_utc"]).to_pylist()]))
    checks = validate_saved(table, summaries, annual_prices,
                            replace(parameters, pumping_formulation="continuous"), legacy=True)
    provenance = {"input_sha256": source_hash, "original_model_sha256": audit["script_sha256"],
                  "reference_files_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                             for path in (REFERENCE_DISPATCH, REFERENCE_ANNUAL)},
                  "validation": checks,
                  "mode_note": "Original continuous model inspected; binary modes reconstructed for saved-dispatch checks."}
    print(f"PASS continuous reference: input/parameters and {len(summaries)} saved annual cases match", flush=True)
    return summaries, provenance


def compare_results(reference, summaries, parameters):
    comparisons = []
    for r in summaries:
        c = next(c for c in reference if (c["country"], c["year"]) == (r["country"], r["year"]))
        if r["objective_eur"] > c["objective_upper_bound_eur"] + ANNUAL_TOL:
            raise ValueError(f"Discrete incumbent exceeds continuous upper bound: {r['country']} {r['year']}")
        scale = parameters.generating_power_mw * 1000
        r.update(
            continuous_margin_eur_per_kw_year=c["margin_eur_per_kw_year"],
            continuous_objective_upper_bound_eur=c["objective_upper_bound_eur"],
            continuous_optimality_gap=c["optimality_gap"],
            continuous_equivalent_full_discharge_cycles=c["equivalent_full_discharge_cycles"],
            margin_reduction_eur_per_kw_year=c["margin_eur_per_kw_year"] - r["margin_eur_per_kw_year"],
            # Raw bound intervals are signed, not clipped using the subset relation.
            reduction_lower_bound_eur_per_kw_year=(c["arbitrage_margin_eur"] - r["objective_upper_bound_eur"]) / scale,
            reduction_upper_bound_eur_per_kw_year=(c["objective_upper_bound_eur"] - r["objective_eur"]) / scale,
        )
    for country in COUNTRIES:
        rows = [r for r in summaries if r["country"] == country]
        mean = lambda field: math.fsum(r[field] for r in rows) / len(rows)
        continuous = mean("continuous_margin_eur_per_kw_year")
        reduction = mean("margin_reduction_eur_per_kw_year")
        comparison = dict(country=country, years=len(rows), continuous_margin=continuous,
                          discrete_margin=mean("margin_eur_per_kw_year"), reduction=reduction,
                          reduction_pct=100 * reduction / continuous if continuous else None,
                          continuous_efc=mean("continuous_equivalent_full_discharge_cycles"),
                          discrete_efc=mean("equivalent_full_discharge_cycles"),
                          reduction_lower_bound=mean("reduction_lower_bound_eur_per_kw_year"),
                          reduction_upper_bound=mean("reduction_upper_bound_eur_per_kw_year"))
        comparisons.append(comparison)
        print("COMPARISON", json.dumps(comparison), flush=True)
    return comparisons


def save_outputs(dispatch, summaries, audit, annual_prices, parameters):
    if parameters.pumping_formulation != "discrete":
        raise ValueError("Only new discrete outputs may be written by this comparison script")
    utc = pa.timestamp("us", tz="UTC")
    schema = pa.schema([
        ("country", pa.string()), ("timestamp_utc", utc), ("price_eur_mwh", pa.float64()),
        ("pumping_mw", pa.float64()), ("generation_mw", pa.float64()),
        ("energy_begin_mwh", pa.float64()), ("energy_end_mwh", pa.float64()),
        ("plant_mode", pa.float64()), ("operating_pump_count", pa.float64()),
    ] + [(key, pa.string() if isinstance(value, str) else pa.int64() if isinstance(value, int) else pa.float64())
         for key, value in parameters.metadata().items()],
        metadata={b"phs_baseline_provenance": json.dumps(audit, sort_keys=True).encode()})
    output = STUDY / "data/phs_discrete_baseline_dispatch.parquet"
    table = pa.Table.from_pylist(dispatch, schema=schema)
    validate_saved(table, summaries, annual_prices, parameters)
    pq.write_table(table, output, compression="snappy")
    if not pq.read_table(output).equals(table):
        raise ValueError("Dispatch Parquet round-trip failed")
    annual_output = STUDY / "data/phs_discrete_baseline_annual.csv"
    with annual_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    readback = read_summaries(annual_output)
    if readback != summaries:
        raise ValueError("Annual CSV round-trip failed")
    validate_saved(pq.read_table(output), readback, annual_prices, parameters)
    print(f"PASS saved-output readback: {table.num_rows} dispatch rows, {len(readback)} annual rows", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run only the small checks in both formulations")
    parser.add_argument("--check-reference", action="store_true", help="Validate tests and saved continuous reference, without annual solves")
    parser.add_argument("--pumping-formulation", choices=("continuous", "discrete"), default="discrete",
                        help="Continuous annual runs are in-memory only; original outputs are never overwritten")
    parser.add_argument("--time-limit", type=float, default=300.0, help="Maximum solver seconds per annual case")
    parser.add_argument("--solver-log", action="store_true", help="Print HiGHS solver progress")
    args = parser.parse_args()
    tests = small_checks()
    if args.self_test:
        return
    parameters = Parameters(pumping_formulation=args.pumping_formulation)
    source_hash = hashlib.sha256(INPUT.read_bytes()).hexdigest()
    annual_prices = read_annual_prices(STUDY_YEARS, parameters)
    reference, reference_audit = load_reference(annual_prices, parameters, source_hash)
    if args.check_reference:
        return
    if parameters.pumping_formulation == "discrete" and any(
            (STUDY / "data" / name).exists() for name in
            ("phs_discrete_baseline_dispatch.parquet", "phs_discrete_baseline_annual.csv")):
        raise FileExistsError("Discrete outputs already exist; preserve them before explicitly rerunning")
    audit = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT), "input_sha256": source_hash,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "solver": "HiGHS via scipy.optimize.milp", "scipy_version": scipy.__version__,
        "numpy_version": np.__version__, "pyarrow_version": pa.__version__,
        "mip_rel_gap_target": MIP_GAP, "time_limit_seconds_per_case": args.time_limit,
        **parameters.metadata(), "study_years": list(STUDY_YEARS), "countries": list(COUNTRIES),
        "energy_convention": "discharge-equivalent MWh", "small_checks": tests,
        "annual_validation": {}, "solver_messages": {},
        "continuous_reference": reference_audit,
        "pumping_approximation": "Selected pump combination fixed throughout each interval; one shared reservoir",
    }
    dispatch, summaries = [], []
    for country in COUNTRIES:
        for year in STUDY_YEARS:
            timestamps, prices = annual_prices[(country, year)]
            print(f"Solving {country} {year}: {len(prices)} hours", flush=True)
            result = solve_dispatch(prices, parameters,
                                    time_limit=args.time_limit, solver_log=args.solver_log)
            audit["annual_validation"][f"{country}_{year}"] = result["validation"]
            audit["solver_messages"][f"{country}_{year}"] = result["solver_message"]
            summaries.append({
                "country": country, "year": year, **parameters.metadata(),
                **{field: result[field] for field in METRICS},
                "solver_status": result["solver_status"], "optimality_gap": result["optimality_gap"],
                "hours": len(prices), "solver": f"HiGHS (SciPy {scipy.__version__})",
                "solver_status_code": result["solver_status_code"],
                "objective_upper_bound_eur": result["objective_upper_bound_eur"],
                "objective_eur": result["objective_eur"],
                "solver_seconds": result["solver_seconds"],
                "solver_nodes": result["solver_nodes"],
            })
            for t, timestamp in enumerate(timestamps):
                dispatch.append({
                    "country": country, "timestamp_utc": timestamp, "price_eur_mwh": float(prices[t]),
                    "pumping_mw": float(result["pump"][t]), "generation_mw": float(result["generate"][t]),
                    "energy_begin_mwh": float(result["energy"][t]),
                    "energy_end_mwh": float(result["energy"][t + 1]),
                    "plant_mode": float(result["mode"][t]),
                    "operating_pump_count": None if result["pump_count"] is None else float(result["pump_count"][t]),
                    **parameters.metadata(),
                })
            print(f"PASS {country} {year}: EUR/kW/year={result['margin_eur_per_kw_year']:.6f}, "
                  f"gap={result['optimality_gap']:.8g}, seconds={result['solver_seconds']:.2f}", flush=True)
    if hashlib.sha256(INPUT.read_bytes()).hexdigest() != source_hash:
        raise ValueError("Input price dataset changed during the run")
    for path, expected_hash in reference_audit["reference_files_sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Continuous reference changed during run")
    if parameters.pumping_formulation == "discrete":
        audit["comparisons"] = compare_results(reference, summaries, parameters)
        save_outputs(dispatch, summaries, audit, annual_prices, parameters)
    print(f"\nArithmetic means of {len(STUDY_YEARS)} independently optimised annual outcomes:")
    for country in COUNTRIES:
        annual = [r for r in summaries if r["country"] == country]
        means = {k: math.fsum(r[k] for r in annual) / len(annual) for k in (
            "margin_eur_per_kw_year", "pumped_mwh", "generated_mwh", "equivalent_full_discharge_cycles")}
        print(country, json.dumps(means))


if __name__ == "__main__":
    main()
