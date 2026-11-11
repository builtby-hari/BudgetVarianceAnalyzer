"""Template-based, factual commentary. States what moved and by how much; it does not
assert why (root cause comes from the cost-center owner)."""

from .variance import EXPENSE, REVENUE, status, top_drivers

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def money(v):
    """$1.23M / $267K / $950 (magnitude only)."""
    a = abs(v)
    if a >= 1_000_000:
        return f"${a/1_000_000:.2f}M"
    if round(a / 1_000) >= 1 and round(a) < 999_500:
        return f"${a/1_000:,.0f}K"
    if a >= 999_500:
        return f"${a/1_000_000:.2f}M"
    return f"${a:,.0f}"


def pct_text(p):
    return "n/a" if p is None else f"{abs(p)*100:.1f}%"


def direction(var, kind=EXPENSE):
    if kind == REVENUE:
        return "above" if var > 0 else ("below" if var < 0 else "on")
    return "over" if var > 0 else ("under" if var < 0 else "on")


def phrase(var, pct, ref="budget", kind=EXPENSE):
    """'$180K (24.0%) over budget, unfavorable'. ref names the comparison (budget or forecast)."""
    if abs(var) < 0.5:
        return f"on {ref}"
    fu = "unfavorable" if status(var, kind=kind) == "U" else "favorable"
    return f"{money(var)} ({pct_text(pct)}) {direction(var, kind)} {ref}, {fu}"


def period_text(analysis):
    n = analysis.months_elapsed
    return f"YTD {MONTH_ABBR[0]}-{MONTH_ABBR[n-1]}" if n > 1 else f"YTD {MONTH_ABBR[0]}"


def cost_center_commentary(analysis, cc_row):
    """List of bullet strings for one cost center. Expense lines only, so the wording stays 'over budget'."""
    th = analysis.thresholds
    lines = [l for l in analysis.expense_lines if l.cost_center == cc_row.cost_center]
    notable = lambda l: l.material or abs(l.var) >= th.amount
    drivers = top_drivers(lines, 3)
    out = [f"{period_text(analysis)} spend {money(cc_row.ytd_actual)} vs budget {money(cc_row.ytd_budget)}: "
           f"{phrase(cc_row.var, cc_row.var_pct)}."]
    shown = []
    if drivers and abs(drivers[0].var) >= 0.5:
        d = drivers[0]
        shown.append(d)
        out.append(f"Largest driver: {d.category}, {phrase(d.var, d.var_pct)}"
                   f"{' (material)' if d.material else ''}.")
        for d in drivers[1:]:
            if notable(d):
                shown.append(d)
                out.append(f"Also: {d.category}, {phrase(d.var, d.var_pct)}"
                           f"{' (material)' if d.material else ''}.")
    offsets = [l for l in lines if l.var * cc_row.var < 0 and notable(l) and l not in shown]
    if offsets:
        o = top_drivers(offsets, 1)[0]
        out.append(f"Largest offset to the net variance: {o.category}, {phrase(o.var, o.var_pct)}.")
    mat = [l for l in lines if l.material]
    if mat:
        out.append("Material items (over " + money(th.amount) + f" and {th.pct*100:g}%): "
                   + ", ".join(l.category for l in top_drivers(mat, len(mat))) + ".")
    else:
        out.append(f"No line breaches the materiality test (over {money(th.amount)} and {th.pct*100:g}%).")
    rr = analysis.run_rate_months
    basis = f"last {rr} months" if rr > 0 else "YTD"
    how = "YTD plus remaining budget" if analysis.method == "budget-remaining" else f"Run-rate ({basis} average)"
    out.append(f"{how} projects full year at {money(cc_row.projected_fy)} vs budget "
               f"{money(cc_row.fy_budget)}: {phrase(cc_row.proj_var, cc_row.proj_var_pct)}.")
    return out


def all_commentary(analysis):
    return {r.cost_center: cost_center_commentary(analysis, r) for r in analysis.cost_centers}


def forecast_commentary(fa):
    """One rules-based line per material line: YTD vs budget, vs the prior forecast, and the full-year
    outlook vs budget. Same materiality test as the rest of the report; states what moved, not why."""
    out = []
    for r in sorted((r for r in fa.rows if r.material), key=lambda r: (-max(abs(r.ytd_var), abs(r.outlook_var)), r.cost_center, r.category)):
        k = r.kind
        vs_fc = (f"{phrase(r.cmp_var, r.cmp_pct, 'forecast', k)}" if r.cmp_forecast or r.cmp_actual
                 else "no prior forecast to compare")
        out.append(f"{r.cost_center} / {r.category}: YTD {money(r.ytd_actual)}, {phrase(r.ytd_var, r.ytd_pct, 'budget', k)}; "
                   f"vs prior forecast, {vs_fc}; full-year outlook {money(r.outlook)} vs budget {money(r.fy_budget)}, "
                   f"{phrase(r.outlook_var, r.outlook_pct, 'budget', k)}.")
    return out
