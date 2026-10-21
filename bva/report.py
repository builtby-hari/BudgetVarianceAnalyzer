"""Static, self-contained HTML report (inline CSS and SVG, no JavaScript, no external assets)."""

import csv
from html import escape

from .charts import bridge_svg, monthly_svg, waterfall_svg
from .commentary import all_commentary, money, period_text, pct_text
from .variance import top_drivers

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
        "<th>Material</th><th>FY Budget</th><th>FY Run-Rate</th><th>Proj. Var $</th><th>Proj. Var %</th></tr>")


def build_html(analysis, top_n=8):
    a, t, th = analysis, analysis.total, analysis.thresholds
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
           f"<div class='tile'><span>Run-rate full year vs budget</span><b class='{t.proj_status}'>{'+' if t.proj_var>0 else '-'}{money(t.proj_var)} ({pct_text(t.proj_var_pct)})</b></div>"
           "</div>",
           "<h2>Cost Center Summary</h2>", f"<table>{HEAD.format('Cost Center')}"]
    out += [_row(r, r.cost_center) for r in a.cost_centers] + [_row(t, "Total"), "</table>",
            f"<div class='note'>Shaded rows breach materiality: |variance| over {money(th.amount)} and over {th.pct*100:g}%. "
            f"Run-rate = YTD actual + average of the {rr} x months remaining.</div>"]
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
    out += ["<h2>Top Drivers (YTD)</h2>", f"<table>{HEAD.format('Cost Center / Category')}"]
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
        w.writerow(CSV_COLUMNS)
        for r in sorted(analysis.lines, key=lambda r: (r.cost_center, r.category)):
            w.writerow(rec(r, "line"))
        for r in analysis.cost_centers:
            w.writerow(rec(r, "cost_center"))
        w.writerow(rec(analysis.total, "total"))
