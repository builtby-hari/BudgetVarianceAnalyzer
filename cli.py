#!/usr/bin/env python3
"""Command-line interface for the budget variance analyzer."""

import argparse
import os

from bva import Thresholds, all_commentary, analyze, load_dataset, is_wide, money, top_drivers
from bva.charts import write_svgs
from bva.report import write_csv, write_html

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
    p.add_argument("--top", type=int, default=5, help="top drivers to show")
    p.add_argument("--no-commentary", action="store_true")
    p.add_argument("--csv", help="write line-level variance CSV")
    p.add_argument("--html", help="write the static HTML report")
    p.add_argument("--charts", help="write the SVG charts (waterfall, monthly, bridge) into this folder")
    return p


def _p(x):
    return "   n/a" if x is None else f"{x*100:+6.1f}%"


def main(argv=None):
    a = build_parser().parse_args(argv)
    th = Thresholds(a.amount_threshold, a.pct_threshold / 100)
    actuals_path = a.actuals
    if actuals_path is None and not is_wide(a.budget):
        actuals_path = os.path.join(HERE, "data", "actuals.csv")
    budget, actuals, kinds = load_dataset(a.budget, actuals_path)
    an = analyze(budget, actuals, a.through, th, a.run_rate_months, kinds)
    for w in an.warnings:
        print("warning:", w)
    print(f"FY{an.year} budget variance, actuals through {an.through} ({an.months_elapsed} months). "
          f"Variance = actual - budget. Expense: + is over budget (unfavorable). Revenue: + is above budget (favorable).")
    print(f"Materiality: over ${th.amount:,.0f} AND over {th.pct*100:g}%.  Run-rate: avg of last "
          f"{an.run_rate_months or 'all'} month(s).\n")
    print(f"{'Cost center':<18}{'YTD budget':>13}{'YTD actual':>13}{'Var $':>11}{'Var %':>8} F/U {'FY budget':>13}{'FY run-rate':>13}{'Proj var':>11}")
    for r in an.cost_centers + [an.total]:
        print(f"{r.cost_center:<18}{r.ytd_budget:>13,.0f}{r.ytd_actual:>13,.0f}{r.var:>+11,.0f}{_p(r.var_pct)} {r.status:^3}"
              f"{r.fy_budget:>13,.0f}{r.projected_fy:>13,.0f}{r.proj_var:>+11,.0f}")
    if an.has_revenue:
        oi = an.operating_income
        oi_pct = "n/a" if oi.var_pct is None else f"{oi.var_pct*100:+.1f}%"
        print(f"\nOperating income (YTD, revenue less expense): budget {oi.budget:,.0f}, actual {oi.actual:,.0f}, "
              f"favorable {oi.favorable:+,.0f} ({oi_pct} of gross budget {oi.gross_budget:,.0f})")
    print(f"\nTop {a.top} drivers (YTD, by absolute $ variance):")
    for i, r in enumerate(top_drivers(an.lines, a.top), 1):
        print(f"{i:>2}. {r.cost_center} / {r.category:<22}{r.var:>+11,.0f}{_p(r.var_pct)} {r.status}{'  MATERIAL' if r.material else ''}")
    if not a.no_commentary:
        print("\nCommentary:")
        for cc, bullets in all_commentary(an).items():
            print(f" {cc}")
            for b in bullets:
                print(f"  - {b}")
    if a.csv:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        write_csv(an, a.csv)
        print("\nwrote", a.csv)
    if a.html:
        os.makedirs(os.path.dirname(os.path.abspath(a.html)), exist_ok=True)
        write_html(an, a.html)
        print("wrote", a.html)
    if a.charts:
        for name in write_svgs(an, a.charts):
            print("wrote", os.path.join(a.charts, name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
