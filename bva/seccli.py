"""`python cli.py sec ...`: run the variance engine on SEC data. See docs/public-companies.md."""

import argparse
import csv
import dataclasses
import json
import os
import sys

from .commentary import all_commentary, money
from .guidance import evaluate
from .ixbrl import IxbrlError, extract_segments, load_instance
from .report import write_csv, write_html
from .secdata import (SecError, SecDataset, Period, build_dataset, cik_for_ticker, fetch_json, load_json,
                      FACTS_URL, TICKERS_URL, source_record)
from .variance import BUDGET_REMAINING, EXPENSE, REVENUE, Thresholds, analyze, status, top_drivers, variance, variance_pct

SOURCE_COLUMNS = ["line", "role", "value", "form", "period_start", "period_end", "accession", "filed", "tag", "note"]


def build_parser():
    p = argparse.ArgumentParser(prog="cli.py sec", description="Variance analysis on public-company SEC data")
    p.add_argument("--ticker", help="ticker, e.g. HD")
    p.add_argument("--mode", choices=("yoy", "qoq", "guidance", "segments"), default="yoy")
    p.add_argument("--period", choices=("fy", "ytd", "q"), default="fy", help="yoy only: annual 10-K, 10-Q year to date, or latest quarter")
    p.add_argument("--period-end", help="pick the period ending on this date (YYYY-MM-DD) instead of the latest")
    p.add_argument("--facts", help="saved companyfacts JSON (offline). Without it the file is fetched from SEC")
    p.add_argument("--tickers", help="saved company_tickers.json (offline ticker lookup)")
    p.add_argument("--save-facts", help="write the fetched companyfacts JSON here")
    p.add_argument("--guidance", help="guidance CSV (mode guidance)")
    p.add_argument("--instance", help="saved 10-K inline XBRL .htm or XBRL instance .xml (mode segments)")
    p.add_argument("--axis", choices=("segment", "product", "geography"), default="segment")
    p.add_argument("--accession", help="accession number of the filing in --instance (it is not inside the file)")
    p.add_argument("--form", default="10-K")
    p.add_argument("--company", help="company name for labels (segments mode)")
    p.add_argument("--amount-threshold", type=float, help="materiality $ (default 0.5%% of baseline revenue)")
    p.add_argument("--pct-threshold", type=float, default=5.0, help="materiality percent (default 5)")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--no-commentary", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--csv", help="write the variance CSV; a .sources.csv file is written beside it")
    p.add_argument("--sources-csv")
    p.add_argument("--xlsx")
    p.add_argument("--html")
    p.add_argument("--charts")
    return p


def _p(x):
    return "   n/a" if x is None else f"{x*100:+6.1f}%"


def _load_facts(a, parser):
    if a.facts:
        return load_json(a.facts)
    if not a.ticker:
        parser.error("give --ticker (and --facts for offline use)")
    tickers = load_json(a.tickers) if a.tickers else fetch_json(TICKERS_URL)
    cik = cik_for_ticker(a.ticker, tickers)
    facts = fetch_json(FACTS_URL.format(cik=cik))
    if a.save_facts:
        with open(a.save_facts, "w") as f:
            json.dump(facts, f)
    return facts


def segments_dataset(res, company, ticker, form, accession):
    """Map segment rows into BVA lines. Segment = cost center. Revenue is a revenue line. Where the
    segment has tagged operating income, its operating costs (revenue minus operating income) are an
    expense line, labelled implied, so the cost-center views and heatmap have something to show."""
    month = res.cur_end[:7]
    n, year = int(month[5:7]), month[:4]
    budget, actuals, kinds, sources, lines = {}, {}, {}, [], []

    def put(cc, label, kind, cur, base):
        kinds[(cc, label)] = kind
        for i in range(1, n):
            budget[(cc, label, f"{year}-{i:02d}")] = 0.0
            actuals[(cc, label, f"{year}-{i:02d}")] = 0.0
        budget[(cc, label, month)] = base
        actuals[(cc, label, month)] = cur
        lines.append((cc, label))

    def src(line, role, s, note=""):
        end = s["end"]
        return {"line": line, "role": role, "value": s["value"], "form": form, "period_start": s["start"],
                "period_end": end, "accession": accession or "not given (pass --accession)", "filed": "",
                "tag": s["tag"], "note": f"{note}axis member {s['member']}, context {s['context']}".strip()}

    for r in res.rows:
        if r.revenue_cur is None or r.revenue_base is None:
            continue
        put(r.label, "Revenue", REVENUE, r.revenue_cur, r.revenue_base)
        for s in r.sources:
            if s["tag"] != "us-gaap:OperatingIncomeLoss":
                sources.append(src(f"{r.label} / Revenue", s["role"], s))
        if r.oi_cur is not None and r.oi_base is not None:
            put(r.label, "Operating costs (implied)", EXPENSE, r.revenue_cur - r.oi_cur, r.revenue_base - r.oi_base)
            for s in r.sources:
                sources.append(src(f"{r.label} / Operating costs (implied)", s["role"], s,
                                   "implied cost = segment revenue minus segment operating income; "))
    if not lines:
        raise SecError("no segment had revenue for both years")
    base_rev = sum(r.revenue_base or 0 for r in res.rows)
    cur = Period("FY", f"{int(res.cur_end[:4]) - 1}", res.cur_end)
    meta = {"title": f"{company} ({ticker}): segments, fiscal year ended {res.cur_end} vs {res.base_end}",
            "baseline_word": "prior year", "period_label": f"fiscal year ended {res.cur_end}", "no_projection": True,
            "segments": True, "note": f"{form} accession {accession or 'not given'}; axis {res.axis}; budget column = prior fiscal year "
                    f"(ended {res.base_end}); a segment's operating costs are implied (revenue less operating income)",
            "chart_label": f"{ticker} segments FY ended {res.cur_end} vs prior year"}
    return budget, actuals, kinds, sources, meta, base_rev


def _thresholds(a, base_rev):
    amt = a.amount_threshold if a.amount_threshold is not None else (round(abs(base_rev) * 0.005, -6) if base_rev else 0.01)
    return Thresholds(amt, a.pct_threshold / 100)


def _write_sources(path, sources):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, SOURCE_COLUMNS)
        w.writeheader()
        for s in sources:
            w.writerow({**s, "value": f"{s['value']:.2f}"})


def _print_memo(memo, say):
    say("\nBelow the operating line (memo, same comparison):")
    say(f"{'':<34}{'Baseline':>18}{'Actual':>18}{'Var':>18}{'Var %':>8} F/U")
    for m in memo:
        v = variance(m["cur"], m["base"])
        pct = "n/a" if m["base"] == 0 else f"{(m['cur']-m['base'])/abs(m['base'])*100:+.1f}%"
        f = (lambda x, sign=False: format(x, "+,.2f" if sign else ",.2f")) if m["key"] == "diluted_eps" \
            else (lambda x, sign=False: format(x, "+,.0f" if sign else ",.0f"))
        say(f"{m['label']:<34}{f(m['base']):>18}{f(m['cur']):>18}{f(v, True):>18}{pct:>8} {status(v, kind=m['kind'])}")


def _print_sources(sources, say):
    say("\nSources (form, period, accession, tag):")
    seen = set()
    for s in sources:
        key = (s["line"], s["form"], s["period_end"], s["accession"], s["tag"], s["role"], s["value"])
        if key in seen:
            continue
        seen.add(key)
        say(f"  {s['line'][:44]:<44} {s['role']:<8} {s['value']:>18,.2f}  {s['form']} {s['period_start']}..{s['period_end']}"
            f"  acc {s['accession']}  {s['tag']}" + (f"  {s['note']}" if s["role"] == "guidance" else ""))


def _run_engine(a, budget, actuals, kinds, through, base_rev, sources, meta, warnings):
    th = _thresholds(a, base_rev)
    an = analyze(budget, actuals, through, th, 3, kinds, BUDGET_REMAINING)
    an.sources, an.meta = sources, meta
    an.warnings = list(warnings) + an.warnings
    return an, th


def _report(a, an, th, memo, say):
    for w in an.warnings:
        say("warning:", w)
    say(an.meta["title"])
    say(an.meta["note"])
    say("Variance = actual - baseline. Expense: + is above baseline (unfavorable). Revenue: + is above baseline (favorable).")
    say(f"Materiality: over {money(th.amount)} AND over {th.pct*100:g}%. No full-year projection (a filed period is complete).\n")
    say(f"{'Cost center':<38}{'Baseline':>16}{'Actual':>16}{'Var $':>16}{'Var %':>8} F/U")
    for r in an.cost_centers + [an.total]:
        say(f"{r.cost_center[:37]:<38}{r.ytd_budget:>16,.0f}{r.ytd_actual:>16,.0f}{r.var:>+16,.0f}{_p(r.var_pct)} {r.status:^3}")
    for r in an.lines:
        if r.kind == REVENUE:
            say(f"{(r.cost_center + ' / ' + r.category)[:37]:<38}{r.ytd_budget:>16,.0f}{r.ytd_actual:>16,.0f}{r.var:>+16,.0f}{_p(r.var_pct)} {r.status:^3}{'  MATERIAL' if r.material else ''}")
    oi = an.operating_income
    rc = an.meta.get("reconciliation", {"ok": True})
    if rc["ok"]:
        say(f"\nOperating income (revenue less expense lines): baseline {oi.budget:,.0f}, actual {oi.actual:,.0f}, "
            f"change {oi.favorable:+,.0f}")
    else:
        say("\nOperating income as reported: "
            f"baseline {rc['reported_base']:,.0f}, actual {rc['reported_cur']:,.0f}, "
            f"change {rc['reported_cur'] - rc['reported_base']:+,.0f}")
        say("The lines above do NOT add up to it, so no engine figure is shown. Unreconciled gap (lines minus reported): "
            f"baseline {rc['lines_base'] - rc['reported_base']:+,.0f}, actual {rc['lines_cur'] - rc['reported_cur']:+,.0f}.")
    say(f"\nTop {a.top} drivers by absolute $ variance:")
    drivers = top_drivers(an.lines, a.top)
    width = max((len(f"{r.cost_center} / {r.category}") for r in drivers), default=0)
    for i, r in enumerate(drivers, 1):
        say(f"{i:>2}. {r.cost_center + ' / ' + r.category:<{width}}{r.var:>+17,.0f}{_p(r.var_pct)} {r.status}{'  MATERIAL' if r.material else ''}")
    if memo:
        _print_memo(memo, say)
    if not a.no_commentary:
        say("\nCommentary:")
        for cc, bullets in all_commentary(an).items():
            say(f" {cc}")
            for b in bullets:
                say(f"  - {b}")
    _print_sources(an.sources, say)


def _outputs(a, an, say):
    if a.csv:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        write_csv(an, a.csv)
        say("wrote", a.csv)
    sc = a.sources_csv or (os.path.splitext(a.csv)[0] + ".sources.csv" if a.csv else None)
    if sc:
        _write_sources(sc, an.sources)
        say("wrote", sc)
    if a.xlsx:
        from .xlsx import write_xlsx
        os.makedirs(os.path.dirname(os.path.abspath(a.xlsx)), exist_ok=True)
        write_xlsx(an, an.thresholds, a.xlsx)
        say("wrote", a.xlsx)
    if a.html:
        os.makedirs(os.path.dirname(os.path.abspath(a.html)), exist_ok=True)
        write_html(an, a.html)
        say("wrote", a.html)
    if a.charts:
        from .charts import write_svgs
        for name in write_svgs(an, a.charts):
            say("wrote", os.path.join(a.charts, name))


def main(argv=None):
    parser = build_parser()
    a = parser.parse_args(argv)
    say = (lambda *x, **k: None) if a.json else print
    try:
        if a.mode == "segments":
            if not a.instance:
                parser.error("--mode segments needs --instance")
            inst = load_instance(a.instance)
            res = extract_segments(inst, a.axis)
            ticker = (a.ticker or inst.dei.get("dei:TradingSymbol", "")).upper()
            company = a.company or inst.dei.get("dei:EntityRegistrantName", ticker)
            budget, actuals, kinds, sources, meta, base_rev = segments_dataset(res, company, ticker, a.form, a.accession)
            an, th = _run_engine(a, budget, actuals, kinds, res.cur_end[:7], base_rev, sources, meta, res.warnings)
            memo = []
        else:
            facts = _load_facts(a, parser)
            ds = build_dataset(facts, a.ticker or "", "qoq" if a.mode == "qoq" else "yoy",
                               a.period if a.mode == "yoy" else "q", a.period_end) if a.mode != "guidance" else None
            if a.mode == "guidance":
                return _guidance(a, parser, facts, say)
            an, th = _run_engine(a, ds.budget, ds.actuals, ds.kinds, ds.through, ds.base_revenue, ds.sources, ds.meta, ds.warnings)
            memo = ds.memo
    except (SecError, IxbrlError, OSError, ValueError) as e:
        parser.error(str(e))
    if a.json:
        print(json.dumps({"title": an.meta["title"], "note": an.meta["note"], "lines": [dataclasses.asdict(r) for r in an.lines],
                          "operating_income": dataclasses.asdict(an.operating_income), "reconciliation": an.meta.get("reconciliation"), "warnings": an.warnings,
                          "sources": an.sources}, indent=2))
    else:
        _report(a, an, th, memo, say)
    _outputs(a, an, say)
    return 0


def _guidance(a, parser, facts, say):
    if not a.guidance:
        parser.error("--mode guidance needs --guidance file.csv")
    rows = evaluate(a.guidance, facts)
    company = facts.get("entityName", a.ticker or "")
    say(f"{company}: reported actuals vs company guidance")
    if not rows:
        say("No guidance rows in the file (a blank template). Fill in metric, period_end, low/high or point, and a source.")
        return 0
    say(f"{'Metric':<28}{'Period end':<12}{'Low':>12}{'High':>12}{'Actual':>12}  Position")
    srcs = []
    budget, actuals, kinds, base_rev, through = {}, {}, {}, 0.0, None
    for r in rows:
        f = (lambda v: "" if v is None else f"{v:,.2f}")
        say(f"{r.label[:27]:<28}{r.period_end:<12}{f(r.low):>12}{f(r.high):>12}{f(r.actual):>12}  {r.position}")
        say(f"    source: {r.source}")
        if r.note:
            say(f"    note: {r.note}")
        for fa in r.actual_sources:
            srcs.append(source_record(f"Guidance / {r.label}", "actual", fa, r.note))
        srcs.append({"line": f"Guidance / {r.label}", "role": "guidance", "value": r.mid, "form": "guidance",
                     "period_start": "", "period_end": r.period_end, "accession": "", "filed": "",
                     "tag": "", "note": f"midpoint of {f(r.low)}-{f(r.high)}; cited: {r.source}"})
        if r.actual is not None and r.in_dollar_engine:
            month = r.period_end[:7]
            n, year = int(month[5:7]), month[:4]
            key = ("Guidance", r.label)
            kinds[key] = r.kind
            for i in range(1, n):
                budget[key + (f"{year}-{i:02d}",)] = 0.0
                actuals[key + (f"{year}-{i:02d}",)] = 0.0
            budget[key + (month,)], actuals[key + (month,)] = r.mid, r.actual
            through = through or month
            if r.metric == "revenue":
                base_rev = r.mid
    if not actuals:
        _print_sources(srcs, say)
        return 0
    meta = {"title": f"{company}: actual vs guidance midpoint", "baseline_word": "guidance midpoint",
            "period_label": "guided period", "no_projection": True,
            "note": "Budget column = midpoint of company guidance (cited in the sources); actual = XBRL reported",
            "chart_label": f"{a.ticker or company} actual vs guidance midpoint"}
    through = max(m for (_, _, m) in actuals)
    try:
        an, th = _run_engine(a, budget, actuals, kinds, through, base_rev, srcs, meta, [])
    except ValueError as e:
        say("note:", e)
        _print_sources(srcs, say)
        return 0
    if not a.json:
        if base_rev:
            say(f"\nVariance vs guidance midpoint (materiality over {money(th.amount)} and {th.pct*100:g}%):")
        else:
            say(f"\nVariance vs guidance midpoint (no revenue guidance, so no dollar threshold; material if over {th.pct*100:g}%):")
        say(f"  {'':<36}{'Guidance mid':>16}{'Actual':>16}{'Var':>16}{'Var %':>8} F/U")
        for r in an.lines:
            d = 2 if abs(r.ytd_budget) < 1000 else 0
            say(f"  {r.category:<36}{r.ytd_budget:>16,.{d}f}{r.ytd_actual:>16,.{d}f}{r.var:>+16,.{d}f}{_p(r.var_pct)} {r.status}"
                f"{'  MATERIAL' if r.material else ''}")
        _print_sources(srcs, say)
    _outputs(a, an, say)
    return 0
