"""Variance, materiality, YTD vs full year, run-rate projection, operating income.

Sign convention, by line type:
  expense: actual above budget is unfavorable (positive variance = overspend).
  revenue: actual above budget is favorable (positive variance = more revenue).
The operating-income total is revenue minus expense. Its favorable variance is the change
in operating income, so positive is always favorable there.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MONTHS = 12
EXPENSE, REVENUE = "expense", "revenue"


@dataclass(frozen=True)
class Thresholds:
    amount: float = 25_000.0   # |variance $| must exceed this ...
    pct: float = 0.10          # ... AND |variance %| must exceed this


def variance(actual, budget):
    """Actual minus budget. For expense, positive means overspend; for revenue, positive means a beat."""
    return actual - budget


def variance_pct(actual, budget):
    """Variance as a fraction of budget; None when budget is zero."""
    return None if budget == 0 else (actual - budget) / abs(budget)


def status(var, tol=0.005, kind=EXPENSE):
    """Expense: over budget is Unfavorable, under budget is Favorable.
    Revenue: above budget is Favorable, below budget is Unfavorable."""
    x = var if kind == EXPENSE else -var      # x > 0 means unfavorable
    return "U" if x > tol else ("F" if x < -tol else "-")


def is_material(var, pct, th):
    """Both tests must breach. With a zero budget (pct is None) only the $ test applies."""
    return abs(var) > th.amount and (pct is None or abs(pct) > th.pct)


def run_rate_projection(actual_months, months_remaining, n=3):
    """YTD actual plus the average of the last n closed months for each remaining month.
    n <= 0 (or larger than the months available) averages all closed months."""
    if not actual_months:
        return 0.0
    window = actual_months[-n:] if n > 0 else actual_months
    return sum(actual_months) + (sum(window) / len(window)) * months_remaining


@dataclass
class Row:
    cost_center: str
    category: Optional[str]        # None on cost-center and total rollups
    ytd_budget: float
    ytd_actual: float
    var: float                     # actual - budget (see the module sign convention)
    var_pct: Optional[float]
    status: str
    material: bool
    fy_budget: float
    projected_fy: float
    proj_var: float
    proj_var_pct: Optional[float]
    proj_status: str
    kind: str = EXPENSE

    @property
    def label(self):
        return self.category or self.cost_center

    @property
    def favorable(self):
        """Effect on operating income: positive is favorable, for either type."""
        return self.var if self.kind == REVENUE else -self.var


@dataclass(frozen=True)
class OperatingIncome:
    budget: float        # YTD revenue minus expense, budget
    actual: float        # YTD revenue minus expense, actual
    favorable: float     # actual - budget: positive increases operating income
    gross_budget: float  # sum of |YTD budget| over all lines: the base for the percent

    @property
    def var_pct(self):
        """Gross budget is the base: dividing by a net figure that mixes revenue and expense is meaningless."""
        return None if self.gross_budget == 0 else self.favorable / self.gross_budget


def summarize_operating_income(lines):
    sign = lambda r: 1 if r.kind == REVENUE else -1
    b = sum(sign(r) * r.ytd_budget for r in lines)
    a = sum(sign(r) * r.ytd_actual for r in lines)
    gross = sum(abs(r.ytd_budget) for r in lines)
    return OperatingIncome(b, a, a - b, gross)


def bridge_steps(analysis):
    """(label, value, kind): budget operating income, one favorable step per category (largest first),
    then actual operating income. kind is 'total' or 'delta'. The deltas sum to actual minus budget."""
    oi = analysis.operating_income
    groups: Dict[str, float] = {}
    for r in analysis.lines:
        groups[r.category] = groups.get(r.category, 0.0) + r.favorable
    steps = [("Budget OI", oi.budget, "total")]
    steps += [(k, v, "delta") for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))]
    steps.append(("Actual OI", oi.actual, "total"))
    return steps


@dataclass
class Analysis:
    through: str
    year: int
    months_elapsed: int
    thresholds: Thresholds
    run_rate_months: int
    lines: List[Row]
    cost_centers: List[Row]        # expense lines only
    total: Row                     # expense lines only
    monthly: List[dict]            # month, budget, actual (None if open), projection (None if closed)
    warnings: List[str] = field(default_factory=list)
    operating_income: Optional[OperatingIncome] = None
    cc_monthly: Dict[str, List[dict]] = field(default_factory=dict)  # expense cost center -> month, budget, actual

    @property
    def expense_lines(self):
        return [r for r in self.lines if r.kind == EXPENSE]

    @property
    def has_revenue(self):
        """Operating income and its bridge only mean something when revenue lines are present."""
        return any(r.kind == REVENUE for r in self.lines)


def make_row(cc, cat, bud_m, act_m, th, rr_months, kind=EXPENSE):
    """Build a Row from 12 monthly budgets and the closed months' actuals."""
    n = len(act_m)
    ytd_b, ytd_a = sum(bud_m[:n]), sum(act_m)
    var = variance(ytd_a, ytd_b)
    pct = variance_pct(ytd_a, ytd_b)
    fy_b = sum(bud_m)
    proj = run_rate_projection(act_m, MONTHS - n, rr_months)
    pvar = variance(proj, fy_b)
    return Row(cc, cat, ytd_b, ytd_a, var, pct, status(var, kind=kind), is_material(var, pct, th),
               fy_b, proj, pvar, variance_pct(proj, fy_b), status(pvar, kind=kind), kind)


def analyze(budget: Dict[Tuple[str, str, str], float], actuals: Dict[Tuple[str, str, str], float],
            through: Optional[str] = None, thresholds: Thresholds = Thresholds(),
            run_rate_months: int = 3, kinds: Optional[Dict[Tuple[str, str], str]] = None) -> Analysis:
    """kinds maps (cost_center, category) to 'expense' or 'revenue'. Lines not in kinds are expense."""
    if not actuals:
        raise ValueError("no actuals loaded")
    kinds = kinds or {}
    kind_of = lambda c, k: kinds.get((c, k), EXPENSE)
    if through is None:
        through = max(m for (_, _, m) in actuals)
    year, n = int(through[:4]), int(through[5:7])
    fy = [f"{year}-{i:02d}" for i in range(1, MONTHS + 1)]
    closed = fy[:n]

    keys = sorted({(c, k) for (c, k, m) in budget if m in fy} | {(c, k) for (c, k, m) in actuals if m in closed})
    warnings, series = [], {}
    for c, k in keys:
        bud = [budget.get((c, k, m), 0.0) for m in fy]
        act = [actuals.get((c, k, m), 0.0) for m in closed]
        gaps = sum(1 for m in closed if (c, k, m) not in actuals)
        if gaps:
            warnings.append(f"{c} / {k}: {gaps} closed month(s) have no actuals (treated as $0)")
        if not any((c, k, m) in budget for m in fy):
            warnings.append(f"{c} / {k}: no budget in FY{year} (treated as $0)")
        series[(c, k)] = (bud, act)

    lines = [make_row(c, k, b, a, thresholds, run_rate_months, kind_of(c, k)) for (c, k), (b, a) in series.items()]
    expense = {key: v for key, v in series.items() if kind_of(*key) == EXPENSE}

    def rollup(name, members):
        bud = [sum(s[0][i] for s in members) for i in range(MONTHS)]
        act = [sum(s[1][i] for s in members) for i in range(n)]
        return bud, act

    # cost-center and total rows summarize expense only; revenue shows in the lines and the operating-income total
    ccs = sorted({c for c, _ in expense})
    cc_rows, cc_monthly = [], {}
    for c in ccs:
        bud, act = rollup(c, [v for (cc, _), v in expense.items() if cc == c])
        cc_rows.append(make_row(c, None, bud, act, thresholds, run_rate_months))
        cc_monthly[c] = [{"month": m, "budget": bud[i], "actual": act[i] if i < n else None} for i, m in enumerate(fy)]
    t_bud, t_act = rollup("Total", list(expense.values()))
    total = make_row("Total", None, t_bud, t_act, thresholds, run_rate_months)

    window = t_act[-run_rate_months:] if run_rate_months > 0 else t_act
    rr = sum(window) / len(window) if window else 0.0
    monthly = [{"month": m, "budget": t_bud[i], "actual": t_act[i] if i < n else None,
                "projection": None if i < n else rr} for i, m in enumerate(fy)]
    return Analysis(through, year, n, thresholds, run_rate_months, lines, cc_rows, total, monthly, warnings,
                    operating_income=summarize_operating_income(lines), cc_monthly=cc_monthly)


def top_drivers(rows, n=5):
    """Largest absolute YTD variances first (ties broken by name for stable output)."""
    return sorted(rows, key=lambda r: (-abs(r.var), r.cost_center, r.category or ""))[:n]
