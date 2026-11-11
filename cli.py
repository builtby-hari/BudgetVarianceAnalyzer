#!/usr/bin/env python3
"""Command-line interface for the budget variance analyzer."""

import argparse
import dataclasses
import json
import os
import re
import sys

from bva import (
    Thresholds,
    all_commentary,
    analyze,
    forecast_analysis,
    forecast_commentary,
    is_wide,
    load_dataset,
    load_forecast,
    top_drivers,
)
from bva.charts import write_svgs
from bva.pvm import load_pvm, price_volume_mix
from bva.report import write_accuracy_csv, write_csv, write_forecast_csv, write_html
from bva.xlsx import write_xlsx

HERE = os.path.dirname(os.path.abspath(__file__))


def build_parser():
    p = argparse.ArgumentParser(description="Budget vs actual variance analysis (expense and revenue lines)")
    p.add_argument("--budget", default=os.path.join(HERE, "data", "budget.csv"),
                   help="budget CSV, or a wide CSV with budget and actual columns (then --actuals is not needed)")
    p.add_argument("--actuals", help="actuals CSV (default data/actuals.csv for the narrow layout)")
    p.add_argument("--through", help="last closed month YYYY-MM (default: latest month in actuals)")
    p.add_argument("--amount-threshold", type=float, default=25_000, help="materiality: $ (default 25000)")
    p.add_argument("--pct-threshold", type=float, default=10.0, help="materiality: percent (default 10)")
    p.add_argument("--run-rate-months", type=int, default=3, help="months averaged for projection; 0 = all YTD")
    p.add_argument("--projection", choices=("run-rate", "budget-remaining"), default="run-rate",
                   help="full-year projection: YTD plus recent average (run-rate) or YTD plus remaining budget")
    p.add_argument("--allow-missing-actuals", action="store_true",
                   help="count a closed month with no actuals as $0 (flagged in warnings) instead of failing")
    p.add_argument("--top", type=int, default=5, help="top drivers to show")
    p.add_argument("--fail-on-material", action="store_true",
                   help="exit with status 1 when any line breaches the materiality test (for scripts and CI)")
    p.add_argument("--json", action="store_true", help="print the analysis as JSON instead of the text report")
    p.add_argument("--pvm", help="price/volume/mix CSV (product, budget_qty, budget_price, actual_qty, actual_price); "
                                 "prints the revenue split and exits")
    p.add_argument("--forecast", help="forecast CSV (cost_center, category, month, amount[, as_of]); adds the rolling "
                                      "forecast views: outlook, forecast accuracy and commentary drafts")
    p.add_argument("--forecast-csv", help="write the outlook CSV (needs --forecast)")
    p.add_argument("--accuracy-csv", help="write the forecast accuracy CSV (needs --forecast)")
    p.add_argument("--no-commentary", action="store_true")
    p.add_argument("--csv", help="write line-level variance CSV")
    p.add_argument("--xlsx", help="write an Excel workbook with live variance, materiality and subtotal formulas")
    p.add_argument("--html", help="write the static HTML report")
    p.add_argument("--charts", help="write the SVG charts (waterfall, monthly, bridge) into this folder")
    return p


def _p(x):
    return "   n/a" if x is None else f"{x*100:+6.1f}%"


def _payload(an):
    """JSON-ready view: lines, cost centers, total, operating income and warnings (amounts in dollars, pct as fractions)."""
    return {"through": an.through, "year": an.year, "months_elapsed": an.months_elapsed, "projection": an.method,
            "lines": [dataclasses.asdict(r) for r in an.lines],
            "cost_centers": [dataclasses.asdict(r) for r in an.cost_centers],
            "total": dataclasses.asdict(an.total),
            "operating_income": dataclasses.asdict(an.operating_income) if an.has_revenue else None,
            "forecast": dataclasses.asdict(an.forecast) if an.forecast else None,
            "warnings": an.warnings}


def main(argv=None):
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.through and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", a.through):
        parser.error(f"--through must be YYYY-MM, got {a.through!r}")
    if min(a.run_rate_months, a.amount_threshold, a.pct_threshold, a.top) < 0:
        parser.error("--run-rate-months, thresholds and --top cannot be negative")
    if a.pvm:
        try:
            r = price_volume_mix((bq, bp, aq, ap) for _, bq, bp, aq, ap in load_pvm(a.pvm))
        except (OSError, ValueError) as e:
            parser.error(str(e))
        for label, v in (("Budget revenue", r.budget_revenue), ("Price", r.price), ("Volume", r.volume),
                         ("Mix", r.mix), ("Actual revenue", r.actual_revenue)):
            print(f"{label:<16}{v:>14,.2f}")
        print(f"{'Flexed budget':<16}{r.flexed_revenue:>14,.2f}  (actual units at budget prices)")
        print(f"Price + volume + mix = {r.price + r.volume + r.mix:,.2f} = actual - budget {r.total:,.2f}. Positive is favorable.")
        return 0
    if (a.forecast_csv or a.accuracy_csv) and not a.forecast:
        parser.error("--forecast-csv and --accuracy-csv need --forecast")
    th = Thresholds(a.amount_threshold, a.pct_threshold / 100)
    actuals_path = a.actuals
    try:
        if actuals_path is None and not is_wide(a.budget):
            actuals_path = os.path.join(HERE, "data", "actuals.csv")
        budget, actuals, kinds = load_dataset(a.budget, actuals_path)
    except (OSError, ValueError) as e:
        parser.error(str(e))
    try:
        an = analyze(budget, actuals, a.through, th, a.run_rate_months, kinds, a.projection, a.allow_missing_actuals)
    except ValueError as e:
        parser.error(f"{e} (or pass --allow-missing-actuals)" if "no actuals" in str(e) else str(e))
    if a.forecast:
        try:
            an.forecast = forecast_analysis(an, budget, actuals, load_forecast(a.forecast, kinds), th)
        except (OSError, ValueError) as e:
            parser.error(str(e))
    say = (lambda *x, **k: None) if a.json else print
    for w in an.warnings:
        say("warning:", w)
    say(f"FY{an.year} budget variance, actuals through {an.through} ({an.months_elapsed} month{'s' if an.months_elapsed != 1 else ''}). "
          f"Variance = actual - budget. Expense: + is over budget (unfavorable). Revenue: + is above budget (favorable).")
    say(f"Materiality: over ${th.amount:,.0f} AND over {th.pct*100:g}%.  Projection: "
          + ("YTD plus remaining budget." if an.method == "budget-remaining" else f"run-rate, avg of last {an.run_rate_months or 'all'} month(s).") + "\n")
    say(f"{'Cost center':<18}{'YTD budget':>13}{'YTD actual':>13}{'Var $':>11}{'Var %':>8} F/U {'FY budget':>13}{'FY proj' if an.method != 'run-rate' else 'FY run-rate':>13}{'Proj var':>11}")
    for r in an.cost_centers + [an.total]:
        say(f"{r.cost_center:<18}{r.ytd_budget:>13,.0f}{r.ytd_actual:>13,.0f}{r.var:>+11,.0f}{_p(r.var_pct)} {r.status:^3}"
              f"{r.fy_budget:>13,.0f}{r.projected_fy:>13,.0f}{r.proj_var:>+11,.0f}")
    if an.has_revenue:
        oi = an.operating_income
        oi_pct = "n/a" if oi.var_pct is None else f"{oi.var_pct*100:+.1f}%"
        say(f"\nOperating income (YTD, revenue less expense): budget {oi.budget:,.0f}, actual {oi.actual:,.0f}, "
              f"favorable {oi.favorable:+,.0f} ({oi_pct} of gross budget {oi.gross_budget:,.0f})")
    say(f"\nTop {a.top} drivers (YTD, by absolute $ variance):")
    for i, r in enumerate(top_drivers(an.lines, a.top), 1):
        say(f"{i:>2}. {r.cost_center} / {r.category:<22}{r.var:>+11,.0f}{_p(r.var_pct)} {r.status}{'  MATERIAL' if r.material else ''}")
    fa = an.forecast
    if fa:
        say("\nRolling forecast (outlook = YTD actual + forecast for open months, vs full-year budget):")
        say(f"{'':<38}{'FY budget':>13}{'Outlook':>13}{'Var $':>11}{'Var %':>8} F/U")
        for r in fa.totals + sorted(fa.rows, key=lambda r: (-abs(r.outlook_var), r.cost_center, r.category))[:a.top]:
            say(f"{(r.category and r.cost_center + ' / ' + r.category or r.cost_center)[:38]:<38}"
                f"{r.fy_budget:>13,.0f}{r.outlook:>13,.0f}{r.outlook_var:>+11,.0f}{_p(r.outlook_pct)} {r.status:^3}")
        if fa.accuracy:
            say(f"\nForecast accuracy (prior forecast vs actual, closed months; bias > 0 is over-forecast), worst {a.top} by MAPE:")
            for x in sorted(fa.accuracy, key=lambda x: (-(x.mape or 0), x.cost_center, x.category))[:a.top]:
                say(f"  {x.cost_center} / {x.category:<22}{x.months:>3} mo  MAPE {'n/a' if x.mape is None else f'{x.mape*100:.1f}%':>6}"
                    f"  bias {x.bias:>+11,.0f} {_p(x.bias_pct)}")
            say("  Pooled (MAPE weighted by |actual|; by horizon = months from the version's as_of to the month):")
            for x in fa.accuracy_totals + fa.accuracy_by_horizon:
                say(f"  {x.kind:<8}{'all horizons' if x.horizon is None else f'{x.horizon} mo ahead':<14}{x.n:>4} line-months"
                    f"  MAPE {'n/a' if x.wmape is None else f'{x.wmape*100:.1f}%':>6}  bias {x.bias:>+11,.0f} {_p(x.bias_pct)}")
        else:
            say("\nForecast accuracy: no closed month had a prior forecast version (add an as_of column).")
        for note in fa.notes:
            say("note:", note)
        if not a.no_commentary:
            say("\nForecast commentary drafts:")
            for d in forecast_commentary(fa):
                say(f"  - {d}")
    if not a.no_commentary:
        say("\nCommentary:")
        for cc, bullets in all_commentary(an).items():
            say(f" {cc}")
            for b in bullets:
                say(f"  - {b}")
    if a.json:
        print(json.dumps(_payload(an), indent=2))
    if a.csv:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        write_csv(an, a.csv)
        say("\nwrote", a.csv)
    if a.xlsx:
        os.makedirs(os.path.dirname(os.path.abspath(a.xlsx)), exist_ok=True)
        write_xlsx(an, th, a.xlsx)
        say("\nwrote", a.xlsx)
    for path, writer in ((a.forecast_csv, write_forecast_csv), (a.accuracy_csv, write_accuracy_csv)):
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            writer(an.forecast, path)
            say("wrote", path)
    if a.html:
        os.makedirs(os.path.dirname(os.path.abspath(a.html)), exist_ok=True)
        write_html(an, a.html)
        say("wrote", a.html)
    if a.charts:
        for name in write_svgs(an, a.charts):
            say("wrote", os.path.join(a.charts, name))
    if a.fail_on_material and any(r.material for r in an.lines):
        print("\nmaterial variances found (--fail-on-material)", file=sys.stderr if a.json else sys.stdout)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
