"""Rolling forecast: three-way variance, full-year outlook and forecast accuracy.

Inputs are the monthly budget, the actuals and a forecast file with one or more versions (as_of =
the last closed month when a version was made). Three views per line, same sign convention as
variance.py (expense: over is unfavorable; revenue: above is favorable):
  actual vs forecast   closed months that had a prior forecast (latest version with as_of before the month)
  forecast vs budget   open months, using the latest forecast version made on or before the close
  outlook              YTD actual + open-month forecast, against the full-year budget
Accuracy compares each closed month's prior forecast with its actual: MAPE and bias per line.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .variance import EXPENSE, REVENUE, MONTHS, Thresholds, is_material, status, variance, variance_pct


@dataclass
class ForecastRow:
    cost_center: str
    category: Optional[str]     # None on the total rows
    kind: str
    ytd_budget: float           # closed months, actual vs budget
    ytd_actual: float
    ytd_var: float
    ytd_pct: Optional[float]
    cmp_actual: float           # closed months that had a prior forecast, actual vs forecast
    cmp_forecast: float
    cmp_var: float
    cmp_pct: Optional[float]
    open_budget: float          # open months, forecast vs budget
    open_forecast: float
    open_var: float
    open_pct: Optional[float]
    fy_budget: float
    outlook: float              # YTD actual + open-month forecast
    outlook_var: float
    outlook_pct: Optional[float]
    status: str                 # of the outlook
    material: bool              # YTD vs budget or outlook vs budget breaches the materiality test

    @property
    def label(self):
        return self.category or self.cost_center


@dataclass
class Accuracy:
    cost_center: str
    category: str
    kind: str
    months: int                 # closed months with a prior forecast and an actual
    mape: Optional[float]       # mean of |actual - forecast| / |actual|, months with a nonzero actual; None if none
    bias: float                 # sum of (forecast - actual): positive means over-forecast
    bias_pct: Optional[float]   # bias / sum of actual


@dataclass
class AccuracyTotal:
    """Accuracy pooled over line-months. wmape = sum |actual - forecast| / sum |actual| (MAPE weighted by |actual|,
    so big lines count more than small ones). horizon = months between the version's as_of and the month; None = all."""
    kind: str
    horizon: Optional[int]
    n: int                      # line-months pooled
    wmape: Optional[float]      # None when every actual is zero
    bias: float                 # sum of (forecast - actual): positive means over-forecast
    bias_pct: Optional[float]   # bias / abs(sum of actual)


@dataclass
class ForecastAnalysis:
    rows: List[ForecastRow]       # lines
    totals: List[ForecastRow]     # Total Expense, then Total Revenue when present
    accuracy: List[Accuracy]
    notes: List[str]
    accuracy_totals: List[AccuracyTotal] = field(default_factory=list)    # expense, then revenue, all horizons
    accuracy_by_horizon: List[AccuracyTotal] = field(default_factory=list)  # kind, then horizon ascending


def _month_no(m):
    return int(m[:4]) * 12 + int(m[5:7])


def _pool(kind, horizon, pairs):
    """pairs: (actual, forecast) tuples."""
    act = sum(abs(a) for a, _ in pairs)
    bias, net = sum(p - a for a, p in pairs), sum(a for a, _ in pairs)
    return AccuracyTotal(kind, horizon, len(pairs), sum(abs(a - p) for a, p in pairs) / act if act else None, bias,
                         None if net == 0 else bias / abs(net))


def _row(cc, cat, kind, f, th):
    """Build a ForecastRow from a dict of the summed inputs (so lines and totals share one path)."""
    ytd_var = variance(f["ytd_actual"], f["ytd_budget"])
    ytd_pct = variance_pct(f["ytd_actual"], f["ytd_budget"])
    cmp_var = variance(f["cmp_actual"], f["cmp_forecast"])
    open_var = variance(f["open_forecast"], f["open_budget"])
    outlook = f["ytd_actual"] + f["open_forecast"]
    o_var, o_pct = variance(outlook, f["fy_budget"]), variance_pct(outlook, f["fy_budget"])
    return ForecastRow(cc, cat, kind, f["ytd_budget"], f["ytd_actual"], ytd_var, ytd_pct,
                       f["cmp_actual"], f["cmp_forecast"], cmp_var, variance_pct(f["cmp_actual"], f["cmp_forecast"]),
                       f["open_budget"], f["open_forecast"], open_var, variance_pct(f["open_forecast"], f["open_budget"]),
                       f["fy_budget"], outlook, o_var, o_pct, status(o_var, kind=kind),
                       is_material(ytd_var, ytd_pct, th) or is_material(o_var, o_pct, th))


def forecast_analysis(analysis, budget, actuals, forecasts, th: Optional[Thresholds] = None) -> ForecastAnalysis:
    """analysis: the Analysis for the same data. forecasts: {(cc, cat, month, as_of): amount}.
    A line with no forecast for an open month raises ValueError (fail closed). Versions made after
    the close (as_of past --through) are ignored and noted."""
    th = th or analysis.thresholds
    year, n, through = analysis.year, analysis.months_elapsed, analysis.through
    fy = [f"{year}-{i:02d}" for i in range(1, MONTHS + 1)]
    notes = []
    future = sum(1 for k in forecasts if k[3] is not None and k[3] > through)
    if future:
        notes.append(f"{future} forecast row(s) with as_of after {through} ignored")
    rows, accs, missing = [], [], []
    pooled = {}   # (kind, horizon) -> [(actual, forecast)]
    for line in analysis.lines:
        c, k = line.cost_center, line.category
        f = {"ytd_budget": line.ytd_budget, "ytd_actual": line.ytd_actual, "fy_budget": line.fy_budget,
             "cmp_actual": 0.0, "cmp_forecast": 0.0, "open_budget": 0.0, "open_forecast": 0.0}
        versions = {}   # month -> {as_of: amount}
        for (cc, cat, m, as_of), amt in forecasts.items():
            if (cc, cat) == (c, k) and m in fy and (as_of is None or as_of <= through):
                versions.setdefault(m, {})[as_of] = amt
        errs = []   # (actual, forecast) for closed months with a prior forecast
        for i, m in enumerate(fy):
            v = versions.get(m, {})
            if i >= n:
                if not v:
                    missing.append(f"{c} / {k} {m}")
                    continue
                f["open_budget"] += budget.get((c, k, m), 0.0)
                f["open_forecast"] += v[max(v, key=lambda a: (a is None, a or ""))]   # an unversioned row counts as latest
            elif (c, k, m) in actuals:
                prior = [a for a in v if a is not None and a < m]
                if prior:
                    errs.append((actuals[(c, k, m)], v[max(prior)]))
                    pooled.setdefault((line.kind, _month_no(m) - _month_no(max(prior))), []).append(errs[-1])
        f["cmp_actual"] = sum(a for a, _ in errs)
        f["cmp_forecast"] = sum(p for _, p in errs)
        rows.append(_row(c, k, line.kind, f, th))
        if errs:
            nz = [abs(a - p) / abs(a) for a, p in errs if a != 0]
            bias, tot = sum(p - a for a, p in errs), f["cmp_actual"]
            accs.append(Accuracy(c, k, line.kind, len(errs), sum(nz) / len(nz) if nz else None, bias,
                                 None if tot == 0 else bias / abs(tot)))
    if missing:
        raise ValueError("no forecast for open month(s) (fix the data): " + "; ".join(missing))
    totals = []
    for kind, name in ((EXPENSE, "Total Expense"), (REVENUE, "Total Revenue")):
        part = [r for r in rows if r.kind == kind]
        if part:
            sums = {key: sum(getattr(r, key) for r in part) for key in
                    ("ytd_budget", "ytd_actual", "cmp_actual", "cmp_forecast", "open_budget", "open_forecast", "fy_budget")}
            totals.append(_row(name, None, kind, sums, th))
    kinds = [k for k in (EXPENSE, REVENUE) if any(key[0] == k for key in pooled)]
    acc_totals = [_pool(k, None, [e for key, v in pooled.items() if key[0] == k for e in v]) for k in kinds]
    by_h = [_pool(k, h, pooled[(k, h)]) for k in kinds for h in sorted(h for kk, h in pooled if kk == k)]
    return ForecastAnalysis(rows, totals, accs, notes, acc_totals, by_h)
