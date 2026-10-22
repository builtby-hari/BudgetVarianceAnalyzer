"""Static, self-contained HTML report (inline CSS and SVG, no JavaScript, no external assets)."""

import csv
from html import escape

from .charts import bridge_svg, forecast_accuracy_svg, monthly_svg, outlook_svg, waterfall_svg
from .commentary import all_commentary, forecast_commentary, money, period_text, pct_text
from .variance import METHOD_LABEL, top_drivers

CSS = """
body{font-family:Helvetica,Arial,sans-serif;color:#222;max-width:960px;margin:24px auto;padding:0 16px;line-height:1.45}
h1{font-size:22px;margin-bottom:2px} h2{font-size:17px;margin-top:30px;border-bottom:1px solid #ddd;padding-bottom:4px}
.sub{color:#666;font-size:13px}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.tile{flex:1;min-width:170px;border:1px solid #ddd;border-radius:6px;padding:10px 12px}
.tile b{display:block;font-size:20px} .tile span{font-size:12px;color:#666}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{padding:5px 8px;border-bottom:1px solid #e5e5e5;text-align:right} th{background:#f5f5f5}
th:first-child,td:first-child,td.l,th.l{text-align:left}
.U{color:#b8322e;font-weight:bold} .F{color:#1f7a53;font-weight:bold} .mat{background:#fff4d6}
svg{max-width:100%;height:auto} li{margin:3px 0} .note{font-size:12px;color:#666}
"""


def _d(v):
    return f"{v:,.0f}"


def _sv(var):
    return f"{'+' if var > 0 else ''}{var:,.0f}"


def _pct(p):
    return "n/a" if p is None else f"{p*100:+.1f}%"


def _status(r):
    return {"U": "Unfav", "F": "Fav", "-": "-"}[r.status]


def _row(r, first):
    cls = ' class="mat"' if r.material else ""
    return (f'<tr{cls}><td>{escape(first)}</td><td>{_d(r.ytd_budget)}</td><td>{_d(r.ytd_actual)}</td>'
            f'<td>{_sv(r.var)}</td><td>{_pct(r.var_pct)}</td><td class="{r.status}">{_status(r)}</td>'
            f'<td>{"Yes" if r.material else ""}</td><td>{_d(r.fy_budget)}</td><td>{_d(r.projected_fy)}</td>'
            f'<td>{_sv(r.proj_var)}</td><td>{_pct(r.proj_var_pct)}</td></tr>')


HEAD = ("<tr><th class='l'>{}</th><th>YTD Budget</th><th>YTD Actual</th><th>Var $</th><th>Var %</th><th>F/U</th>"
        "<th>Material</th><th>FY Budget</th><th>FY {}</th><th>Proj. Var $</th><th>Proj. Var %</th></tr>")


def _forecast_html(fa, top_n, analysis):
    def fr(r, first):
        cls = ' class="mat"' if r.material else ""
        return (f'<tr{cls}><td>{escape(first)}</td><td>{_d(r.ytd_budget)}</td><td>{_d(r.ytd_actual)}</td>'
                f'<td>{_d(r.cmp_forecast)}</td><td>{_sv(r.cmp_var)}</td><td>{_d(r.open_budget)}</td>'
                f'<td>{_d(r.open_forecast)}</td><td>{_sv(r.open_var)}</td><td>{_d(r.fy_budget)}</td>'
                f'<td>{_d(r.outlook)}</td><td class="{r.status}">{_sv(r.outlook_var)}</td><td>{_pct(r.outlook_pct)}</td></tr>')
    head = ("<table><tr><th class='l'>{}</th><th>YTD Budget</th><th>YTD Actual</th><th>Prior Forecast</th>"
            "<th>Actual vs Forecast $</th><th>Open Budget</th><th>Open Forecast</th><th>Forecast vs Budget $</th>"
            "<th>FY Budget</th><th>Outlook</th><th>Outlook Var $</th><th>Outlook Var %</th></tr>")
    lines = sorted(fa.rows, key=lambda r: (-abs(r.outlook_var), r.cost_center, r.category))[:top_n]
    out = ["<h2>Rolling Forecast</h2>",
           "<div class='note'>Outlook = YTD actual + forecast for open months, against the full-year budget. "
           "Prior Forecast is the version made before each closed month, for months that had one; "
           "Actual vs Forecast covers those months only. Variance signs follow each line's type.</div>",
           head.format("Total")] + [fr(r, r.cost_center) for r in fa.totals] + ["</table>",
           head.format(f"Top {top_n} Lines by Outlook Variance")]
    out += [fr(r, f"{r.cost_center} / {r.category}") for r in lines] + ["</table>"]
    if fa.accuracy:
        out += ["<h3>Forecast Accuracy</h3>",
                "<table><tr><th class='l'>Line</th><th>Months</th><th>MAPE</th><th>Bias $</th><th>Bias %</th></tr>"]
        for a in sorted(fa.accuracy, key=lambda a: (-(a.mape or 0), a.cost_center, a.category))[:top_n]:
            out.append(f"<tr><td>{escape(a.cost_center)} / {escape(a.category)}</td><td>{a.months}</td>"
                       f"<td>{'n/a' if a.mape is None else f'{a.mape*100:.1f}%'}</td><td>{_sv(a.bias)}</td>"
                       f"<td>{_pct(a.bias_pct)}</td></tr>")
        out += ["</table>", "<table><tr><th class='l'>Pooled Accuracy</th><th>Horizon</th><th>Line-Months</th>"
                "<th>Weighted MAPE</th><th>Bias $</th><th>Bias %</th></tr>"]
        for x in fa.accuracy_totals + fa.accuracy_by_horizon:
            out.append(f"<tr><td>{x.kind.title()}</td><td>{'All' if x.horizon is None else f'{x.horizon} mo ahead'}</td>"
                       f"<td>{x.n}</td><td>{'n/a' if x.wmape is None else f'{x.wmape*100:.1f}%'}</td>"
                       f"<td>{_sv(x.bias)}</td><td>{_pct(x.bias_pct)}</td></tr>")
        out += ["</table>", "<div class='note'>Weighted MAPE pools every line-month: total absolute error over total absolute "
                "actual, so larger lines count more. Horizon is the months from the forecast version's as_of to the month.</div>",
                "<div class='note'>MAPE is the mean absolute percent error of the prior forecast. "
                "Bias is forecast minus actual, so positive means over-forecast.</div>"]
    out += ["<h3>Full-Year Outlook by Driver</h3>", outlook_svg(analysis, fa)]
    if fa.accuracy:
        out += ["<h3>Forecast Accuracy by Line</h3>", forecast_accuracy_svg(fa)]
    drafts = forecast_commentary(fa)
    if drafts:
        out += ["<h3>Commentary Drafts</h3><ul>"] + [f"<li>{escape(d)}</li>" for d in drafts] + ["</ul>"]
    out += [f"<div class='note'>{escape(x)}</div>" for x in fa.notes]
    return out


def build_html(analysis, top_n=8):
    a, t, th = analysis, analysis.total, analysis.thresholds
    label = METHOD_LABEL[a.method]
    rr = f"last {a.run_rate_months} months" if a.run_rate_months > 0 else "YTD"
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Budget Variance Report FY{a.year}</title><style>{CSS}</style></head><body>",
           f"<h1>Budget Variance Report, FY{a.year}</h1>",
           f"<div class='sub'>{escape(period_text(a))} (actuals through {escape(a.through)}). "
           f"Variance = actual - budget. Expense: positive is over budget (unfavorable). "
           f"Revenue: positive is above budget (favorable). Synthetic sample data.</div>",
           "<div class='tiles'>"
           f"<div class='tile'><span>YTD actual</span><b>{money(t.ytd_actual)}</b></div>"
           f"<div class='tile'><span>YTD budget</span><b>{money(t.ytd_budget)}</b></div>"
           f"<div class='tile'><span>YTD variance</span><b class='{t.status}'>{'+' if t.var>0 else '-'}{money(t.var)} ({pct_text(t.var_pct)})</b></div>"
           f"<div class='tile'><span>{label} full year vs budget</span><b class='{t.proj_status}'>{'+' if t.proj_var>0 else '-'}{money(t.proj_var)} ({pct_text(t.proj_var_pct)})</b></div>"
           "</div>",
           "<h2>Cost Center Summary</h2>", f"<table>{HEAD.format('Cost Center', label)}"]
    out += [_row(r, r.cost_center) for r in a.cost_centers] + [_row(t, "Total"), "</table>",
            f"<div class='note'>Shaded rows breach materiality: |variance| over {money(th.amount)} and over {th.pct*100:g}%. "
            + ("Projection = YTD actual + remaining budget.</div>" if a.method == "budget-remaining"
               else f"Run-rate = YTD actual + average of the {rr} x months remaining.</div>")]
    if a.has_revenue:
        oi = a.operating_income
        oi_cls = "F" if oi.favorable > 0 else ("U" if oi.favorable < 0 else "-")
        out += ["<h2>Operating Income (YTD)</h2>",
                "<table><tr><th class='l'>Revenue Less Expense</th><th>Budget OI</th><th>Actual OI</th>"
                "<th>Favorable Var $</th><th>Var % (Gross Budget)</th></tr>",
                f"<tr><td>YTD</td><td>{_d(oi.budget)}</td><td>{_d(oi.actual)}</td>"
                f"<td class='{oi_cls}'>{_sv(oi.favorable)}</td><td>{_pct(oi.var_pct)}</td></tr></table>",
                "<div class='note'>Favorable variance is the change in operating income (positive is favorable). "
                "The percent uses the gross budget, the sum of absolute budgets, as its base.</div>"]
    if a.forecast:
        out += _forecast_html(a.forecast, top_n, a)
    out += ["<h2>Top Drivers (YTD)</h2>", f"<table>{HEAD.format('Cost Center / Category', label)}"]
    out += [_row(r, f"{r.cost_center} / {r.category}") for r in top_drivers(a.lines, top_n)] + ["</table>"]
    out += ["<h2>Budget to Actual by Driver</h2>", waterfall_svg(a),
            ]
    if a.has_revenue:
        out += ["<h2>Operating Income Bridge</h2>", bridge_svg(a)]
    out += ["<h2>Monthly Trend</h2>", monthly_svg(a), "<h2>Commentary</h2>"]
    for cc, bullets in all_commentary(a).items():
        out.append(f"<h3>{escape(cc)}</h3><ul>" + "".join(f"<li>{escape(b)}</li>" for b in bullets) + "</ul>")
    if a.warnings:
        out += ["<h2>Data Warnings</h2><ul>"] + [f"<li>{escape(w)}</li>" for w in a.warnings] + ["</ul>"]
    out.append("<p class='note'>Commentary states what moved and by how much; root causes should come from cost-center owners.</p></body></html>")
    return "\n".join(out)


def write_html(analysis, path):
    with open(path, "w") as f:
        f.write(build_html(analysis))


CSV_COLUMNS = ["cost_center", "category", "level", "ytd_budget", "ytd_actual", "variance", "variance_pct",
               "fav_unfav", "material", "fy_budget", "projected_fy_run_rate", "projected_variance",
               "projected_variance_pct", "type"]
PROJECTED_FY = 9   # index of the projected_fy column; its name follows the projection method


def write_csv(analysis, path):
    """One row per cost center x category (expense and revenue, see the type column), then expense
    cost-center subtotals, then the expense total. fav_unfav follows each line's type."""
    def rec(r, level):
        return [r.cost_center, r.category or "", level, f"{r.ytd_budget:.2f}", f"{r.ytd_actual:.2f}", f"{r.var:.2f}",
                "" if r.var_pct is None else f"{r.var_pct:.4f}", {"U": "Unfavorable", "F": "Favorable", "-": "On budget"}[r.status],
                "Y" if r.material else "N", f"{r.fy_budget:.2f}", f"{r.projected_fy:.2f}", f"{r.proj_var:.2f}",
                "" if r.proj_var_pct is None else f"{r.proj_var_pct:.4f}", r.kind]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        header = list(CSV_COLUMNS)
        header[PROJECTED_FY] = "projected_fy_" + analysis.method.replace("-", "_")
        w.writerow(header)
        for r in sorted(analysis.lines, key=lambda r: (r.cost_center, r.category)):
            w.writerow(rec(r, "line"))
        for r in analysis.cost_centers:
            w.writerow(rec(r, "cost_center"))
        w.writerow(rec(analysis.total, "total"))


def _opt(x, spec):
    return "" if x is None else format(x, spec)


def write_forecast_csv(fa, path):
    """Outlook rows: one per line, then Total Expense and Total Revenue. Variances follow each line's type."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cost_center", "category", "level", "type", "ytd_budget", "ytd_actual", "prior_forecast",
                    "actual_vs_forecast", "actual_vs_forecast_pct", "open_budget", "open_forecast",
                    "forecast_vs_budget", "forecast_vs_budget_pct", "fy_budget", "outlook", "outlook_variance",
                    "outlook_variance_pct", "fav_unfav", "material"])
        for level, rows in (("line", sorted(fa.rows, key=lambda r: (r.cost_center, r.category))), ("total", fa.totals)):
            for r in rows:
                w.writerow([r.cost_center, r.category or "", level, r.kind, f"{r.ytd_budget:.2f}", f"{r.ytd_actual:.2f}",
                            f"{r.cmp_forecast:.2f}", f"{r.cmp_var:.2f}", _opt(r.cmp_pct, ".4f"), f"{r.open_budget:.2f}",
                            f"{r.open_forecast:.2f}", f"{r.open_var:.2f}", _opt(r.open_pct, ".4f"), f"{r.fy_budget:.2f}",
                            f"{r.outlook:.2f}", f"{r.outlook_var:.2f}", _opt(r.outlook_pct, ".4f"),
                            {"U": "Unfavorable", "F": "Favorable", "-": "On budget"}[r.status], "Y" if r.material else "N"])


def write_accuracy_csv(fa, path):
    """Forecast accuracy by line. Bias is forecast minus actual (positive = over-forecast)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cost_center", "category", "type", "months", "mape", "bias", "bias_pct"])
        for a in sorted(fa.accuracy, key=lambda a: (a.cost_center, a.category)):
            w.writerow([a.cost_center, a.category, a.kind, a.months, _opt(a.mape, ".4f"), f"{a.bias:.2f}",
                        _opt(a.bias_pct, ".4f")])
