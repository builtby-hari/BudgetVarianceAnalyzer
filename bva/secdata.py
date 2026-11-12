"""SEC companyfacts to BVA input. Public companies do not publish budgets, so the "budget" side is
something they do publish: the same period last year (yoy), the previous quarter (qoq), or guidance
the user typed in (see guidance.py). Standard library only.

Every number carries its source (form, period, accession, tag). Nothing is estimated: a line that
cannot be tied to a filed value is left out and listed in the warnings.
"""

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .variance import EXPENSE, REVENUE

USER_AGENT = "BudgetVarianceAnalyzer research tool admin@example.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")
UNITS = ("USD", "USD/shares")

REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet",
                "SalesRevenueGoodsNet"]
COST_TAGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
SGA_TAGS = ["SellingGeneralAndAdministrativeExpense"]
SELLING_TAGS = ["SellingAndMarketingExpense", "MarketingAndAdvertisingExpense"]
GA_TAGS = ["GeneralAndAdministrativeExpense"]
RD_TAGS = ["ResearchAndDevelopmentExpense"]
DA_TAGS = ["DepreciationAndAmortization", "DepreciationDepletionAndAmortization",
           "DepreciationAmortizationAndAccretionNet"]
OPEX_TAGS = ["OperatingExpenses", "CostsAndExpenses"]
GP_TAGS = ["GrossProfit"]
OI_TAGS = ["OperatingIncomeLoss"]
INTEREST_TAGS = ["InterestExpenseNonoperating", "InterestExpense", "InterestExpenseDebt"]
PRETAX_TAGS = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
TAX_TAGS = ["IncomeTaxExpenseBenefit"]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss"]
EPS_TAGS = ["EarningsPerShareDiluted"]

# guidance metric -> (tags, type). Keys are what a guidance CSV may use in its metric column.
METRICS = {
    "revenue": (REVENUE_TAGS, REVENUE), "gross_profit": (GP_TAGS, REVENUE),
    "cost_of_revenue": (COST_TAGS, EXPENSE), "sga": (SGA_TAGS, EXPENSE), "rd": (RD_TAGS, EXPENSE),
    "operating_expenses": (OPEX_TAGS[:1], EXPENSE), "operating_income": (OI_TAGS, REVENUE),
    "interest_expense": (INTEREST_TAGS, EXPENSE), "pretax_income": (PRETAX_TAGS, REVENUE),
    "income_tax": (TAX_TAGS, EXPENSE), "net_income": (NI_TAGS, REVENUE), "diluted_eps": (EPS_TAGS, REVENUE),
    "d_and_a": (DA_TAGS, EXPENSE),
}
LABELS = {"revenue": "Revenue", "gross_profit": "Gross profit", "cost_of_revenue": "Cost of revenue",
          "sga": "Selling, general and administrative", "rd": "Research and development",
          "operating_expenses": "Total operating expenses", "operating_income": "Operating income",
          "interest_expense": "Interest expense", "pretax_income": "Income before income taxes",
          "income_tax": "Income taxes", "net_income": "Net income", "diluted_eps": "Diluted EPS",
          "d_and_a": "Depreciation and amortization"}


class SecError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    tag: str
    start: str
    end: str
    val: float
    accn: str
    form: str
    filed: str
    fy: Optional[int] = None
    fp: Optional[str] = None


@dataclass(frozen=True)
class Period:
    kind: str            # FY, Q or YTD
    start: str
    end: str
    derived: bool = False   # a fourth quarter: FY minus nine-month YTD


@dataclass
class Line:
    key: str
    label: str
    cost_center: str
    kind: str
    cur: float
    base: float
    tag: str
    cur_src: List[Fact]
    base_src: List[Fact]
    derived: str = ""        # how a derived line was built, empty for a tagged line


@dataclass
class SecDataset:
    company: str
    ticker: str
    mode: str
    cur: Period
    base: Period
    budget: Dict[Tuple[str, str, str], float]
    actuals: Dict[Tuple[str, str, str], float]
    kinds: Dict[Tuple[str, str], str]
    sources: List[dict]
    memo: List[dict]
    warnings: List[str]
    lines: List[Line]
    through: str
    base_revenue: float
    meta: dict = field(default_factory=dict)


# ---- fetching and loading --------------------------------------------------------------------------

def fetch_json(url, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with opener(req, timeout=60) as resp:
        return json.loads(resp.read())


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cik_for_ticker(ticker, tickers):
    want = ticker.strip().upper().replace(".", "-")
    for row in tickers.values():
        if row.get("ticker", "").upper() == want:
            return int(row["cik_str"])
    raise SecError(f"ticker {ticker!r} not found in company_tickers.json")


def index_facts(companyfacts):
    """{tag: [Fact]} for duration facts in 10-K and 10-Q filings (amendments included)."""
    out: Dict[str, List[Fact]] = {}
    for tag, node in companyfacts.get("facts", {}).get("us-gaap", {}).items():
        units = node.get("units", {})
        unit = next((u for u in UNITS if u in units), None)
        if not unit:
            continue
        for x in units[unit]:
            if x.get("form") in FORMS and x.get("start") and x.get("end"):
                out.setdefault(tag, []).append(Fact(tag, x["start"], x["end"], float(x["val"]), x["accn"],
                                                    x["form"], x.get("filed", ""), x.get("fy"), x.get("fp")))
    return out


def _d(s):
    return date.fromisoformat(s)


def _days(start, end):
    return (_d(end) - _d(start)).days


def _dur_kind(start, end):
    n = _days(start, end)
    if 80 <= n <= 100:
        return "Q"
    if 340 <= n <= 380:
        return "FY"
    if 150 <= n <= 290:
        return "YTD"
    return None


def pick(index, tag, start, end):
    """The fact for exactly this period. When several filings carry it, the latest filed wins
    (it holds any restatement)."""
    hits = [f for f in index.get(tag, ()) if f.start == start and f.end == end]
    return max(hits, key=lambda f: (f.filed, f.accn)) if hits else None


def value(index, tag, period):
    """(value, [facts], note) for a tag over a period, or None. A derived fourth quarter is FY minus 9M."""
    if not period.derived:
        f = pick(index, tag, period.start, period.end)
        return (f.val, [f], "") if f else None
    ytd_end = (_d(period.start) - timedelta(days=1)).isoformat()
    for start in sorted({f.start for f in index.get(tag, ()) if f.end == period.end and _dur_kind(f.start, f.end) == "FY"}):
        full, ytd = pick(index, tag, start, period.end), pick(index, tag, start, ytd_end)
        if full and ytd:
            return (full.val - ytd.val, [full, ytd],
                    f"fourth quarter derived: FY ({full.accn}) minus nine-month YTD ({ytd.accn})")
    return None


def anchor_facts(index):
    out = []
    for tag in REVENUE_TAGS + OI_TAGS + NI_TAGS:
        out += index.get(tag, [])
    return out


def find_periods(index, mode, kind="fy", period_end=None):
    """(current, baseline) periods. mode yoy: kind fy, ytd or q, baseline one year earlier.
    mode qoq: latest quarter against the quarter before it."""
    facts = anchor_facts(index)
    if not facts:
        raise SecError("no revenue, operating income or net income facts found (is this the right company file?)")
    cands: Dict[Tuple[str, str, str, bool], None] = {}
    for f in facts:
        k = _dur_kind(f.start, f.end)
        if k:
            cands[(k, f.start, f.end, False)] = None
    # fourth quarters: FY minus nine months, only when the nine-month figure exists
    for (k, s, e, _d_) in list(cands):
        if k != "FY":
            continue
        for (k2, s2, e2, _x) in list(cands):
            if k2 == "YTD" and s2 == s and 262 <= _days(s2, e2) <= 285 and 80 <= _days(e2, e) <= 100:
                cands[("Q", (_d(e2) + timedelta(days=1)).isoformat(), e, True)] = None
    periods = [Period(*c) for c in cands]
    want = "Q" if mode == "qoq" else kind.upper()
    pool = sorted((p for p in periods if p.kind == want), key=lambda p: p.end)
    if period_end:
        pool = [p for p in pool if p.end == period_end]
    if not pool:
        raise SecError(f"no {want} period found" + (f" ending {period_end}" if period_end else ""))
    cur = pool[-1]
    allp = [p for p in periods if p.kind == want]
    if mode == "qoq":
        base = [p for p in allp if 84 <= _days(p.end, cur.end) <= 100]
    else:
        base = [p for p in allp if 357 <= _days(p.end, cur.end) <= 372
                and abs(_days(p.start, p.end) - _days(cur.start, cur.end)) <= 10]
    if not base:
        raise SecError(f"no comparison period for {want} ending {cur.end}")
    return cur, max(base, key=lambda p: p.end)


def period_label(p):
    names = {"FY": "fiscal year", "Q": "quarter", "YTD": "year to date"}
    return f"{names[p.kind]} {p.start} to {p.end}" + (" (derived)" if p.derived else "")


def source_record(line, role, fact, note=""):
    return {"line": line, "role": role, "value": fact.val, "form": fact.form, "period_start": fact.start,
            "period_end": fact.end, "accession": fact.accn, "filed": fact.filed, "tag": "us-gaap:" + fact.tag,
            "note": note}


def _first_tag(index, tags, cur, base):
    for tag in tags:
        a, b = value(index, tag, cur), value(index, tag, base)
        if a and b:
            return tag, a, b
    return None


def revenue_choice(index, cur, base, tags=None):
    """Revenue tag that has both periods. Companies can file several revenue tags (for example
    RevenueFromContractWithCustomer... for contract revenue and Revenues for the full total). When more than one
    has both periods, the larger current-period total wins and the gap is reported, so a subset tag never
    silently understates revenue. Returns (tag, cur, base, warning) or None."""
    found = []
    for tag in (tags or REVENUE_TAGS):
        a, b = value(index, tag, cur), value(index, tag, base)
        if a and b:
            found.append((tag, a, b))
    if not found:
        return None
    best = max(found, key=lambda t: t[1][0])     # max keeps the earlier tag on a tie
    warn = ""
    others = [t for t in found if t[1][0] != best[1][0]]
    if others:
        warn = ("revenue tags disagree for the current period: used " + best[0] + f" ({best[1][0]:,.0f}), the larger; "
                + "; ".join(f"{t[0]} is {t[1][0]:,.0f}" for t in others)
                + ". The smaller tag is a subset (usually contract revenue only).")
    return best[0], best[1], best[2], warn


def fiscal_label(index, period):
    """The company's own fiscal label for a period from the fy/fp fields, such as 'fiscal 2025' or 'Q3 fiscal 2025'.
    fy is the year of the filing, so a comparative figure carries a later fy than its own year; the earliest fy
    across the filings that report this exact period is the year it belongs to. Empty when unknown."""
    if period.derived:
        end_facts = [f for t in anchor_tags() for f in index.get(t, ()) if f.end == period.end and _dur_kind(f.start, f.end) == "FY"]
        fp = "Q4"
    else:
        end_facts = [f for t in anchor_tags() for f in index.get(t, ()) if f.start == period.start and f.end == period.end]
        fp = None
    end_facts = [f for f in end_facts if f.fy]
    if not end_facts:
        return ""
    fy = min(f.fy for f in end_facts)
    if fp is None:
        fp = next((f.fp for f in end_facts if f.fy == fy and f.fp), "")
    if period.kind == "FY" and not period.derived:
        return f"fiscal {fy}"
    return f"{fp} fiscal {fy}" if fp else f"fiscal {fy}"


def anchor_tags():
    return REVENUE_TAGS + OI_TAGS + NI_TAGS


# ---- statement mapping -----------------------------------------------------------------------------

def build_lines(index, cur, base):
    """Income statement lines that add up to operating income, plus warnings. Revenue is a revenue line;
    cost of revenue and operating expenses are expense lines. Interest, tax and net income are memo only
    (see memo_lines) so the engine's operating income is the reported operating income."""
    warnings, lines = [], []

    def add(key, label, cc, kind, tag, a, b, derived=""):
        lines.append(Line(key, label, cc, kind, a[0], b[0], tag, a[1], b[1], derived or a[2] or b[2]))

    rev = revenue_choice(index, cur, base)
    if not rev:
        raise SecError("no revenue found for both periods")
    if rev[3]:
        warnings.append(rev[3])
    add("revenue", "Revenue", "Revenue", REVENUE, rev[0], rev[1], rev[2])
    cost = _first_tag(index, COST_TAGS, cur, base)
    if cost:
        add("cost_of_revenue", "Cost of revenue", "Cost of Revenue", EXPENSE, cost[0], cost[1], cost[2])
    else:
        gp = _first_tag(index, GP_TAGS, cur, base)
        if gp:
            a = (rev[1][0] - gp[1][0], rev[1][1] + gp[1][1], "")
            b = (rev[2][0] - gp[2][0], rev[2][1] + gp[2][1], "")
            add("cost_of_revenue", "Cost of revenue (derived)", "Cost of Revenue", EXPENSE, gp[0], a, b,
                "derived: revenue minus gross profit")
        else:
            warnings.append("no cost of revenue or gross profit tag for both periods; left out")
    core = []
    sga = _first_tag(index, SGA_TAGS, cur, base)
    if sga:
        core.append(("sga", "Selling, general and administrative", sga))
    else:
        for key, label, tags in (("selling", "Selling and marketing", SELLING_TAGS),
                                 ("ga", "General and administrative", GA_TAGS)):
            t = _first_tag(index, tags, cur, base)
            if t:
                core.append((key, label, t))
    rd = _first_tag(index, RD_TAGS, cur, base)
    if rd:
        core.append(("rd", "Research and development", rd))
    for key, label, t in core:
        add(key, label, "Operating Expenses", EXPENSE, t[0], t[1], t[2])
    opex = _first_tag(index, OPEX_TAGS[:1], cur, base)
    if opex:
        rem_a = opex[1][0] - sum(t[1][0] for _, _, t in core)
        rem_b = opex[2][0] - sum(t[2][0] for _, _, t in core)
        da = _first_tag(index, DA_TAGS, cur, base)
        if da and abs(rem_a - da[1][0]) < 1 and abs(rem_b - da[2][0]) < 1:
            add("da", "Depreciation and amortization", "Operating Expenses", EXPENSE, da[0], da[1], da[2])
        elif abs(rem_a) >= 1 or abs(rem_b) >= 1:
            srcs_a, srcs_b = list(opex[1][1]), list(opex[2][1])
            for _, _, t in core:
                srcs_a += t[1][1]
                srcs_b += t[2][1]
            add("other_opex", "Other operating expenses (derived)", "Operating Expenses", EXPENSE, opex[0],
                (rem_a, srcs_a, ""), (rem_b, srcs_b, ""),
                "derived: total operating expenses minus the itemized lines above")
    else:
        warnings.append("no total operating expenses tag; operating expense lines may be incomplete")
    oi = _first_tag(index, OI_TAGS, cur, base)
    if oi:
        sign = lambda l: 1 if l.kind == REVENUE else -1
        for which, rep in (("current", oi[1][0]), ("baseline", oi[2][0])):
            mine = sum(sign(l) * (l.cur if which == "current" else l.base) for l in lines)
            if abs(mine - rep) > max(1.0, 0.0005 * abs(lines[0].cur)):
                warnings.append(f"lines do not reconcile to reported operating income for the {which} period "
                                f"(lines {mine:,.0f} vs reported {rep:,.0f}); this statement layout needs a manual look")
    else:
        warnings.append("no OperatingIncomeLoss tag; could not check that the lines reconcile")
    return lines, warnings


def memo_lines(index, cur, base):
    """Subtotals and below-the-line items, shown beside the engine output rather than inside it."""
    out = []
    specs = [("operating_income", OI_TAGS, REVENUE), ("interest_expense", INTEREST_TAGS, EXPENSE),
             ("pretax_income", PRETAX_TAGS, REVENUE), ("income_tax", TAX_TAGS, EXPENSE),
             ("net_income", NI_TAGS, REVENUE), ("diluted_eps", EPS_TAGS, REVENUE),
             ("d_and_a", DA_TAGS, EXPENSE)]
    for key, tags, kind in specs:
        t = _first_tag(index, tags, cur, base)
        if t:
            note = "cash flow figure, may sit inside other lines" if key == "d_and_a" else ""
            out.append({"key": key, "label": LABELS[key], "kind": kind, "cur": t[1][0], "base": t[2][0],
                        "tag": t[0], "cur_src": t[1][1], "base_src": t[2][1], "note": note})
    return out


def _month(end):
    return end[:7]


def to_bva(lines, cur, base):
    """Map lines into BVA's keyed dicts. Both sides sit in the current period's end month, with $0
    in the earlier months of that calendar year so the engine sees a closed month for every line."""
    month = _month(cur.end)
    n = int(month[5:7])
    year = month[:4]
    budget, actuals, kinds = {}, {}, {}
    for l in lines:
        kinds[(l.cost_center, l.label)] = l.kind
        for i in range(1, n):
            budget[(l.cost_center, l.label, f"{year}-{i:02d}")] = 0.0
            actuals[(l.cost_center, l.label, f"{year}-{i:02d}")] = 0.0
        budget[(l.cost_center, l.label, month)] = l.base
        actuals[(l.cost_center, l.label, month)] = l.cur
    return budget, actuals, kinds


def build_dataset(companyfacts, ticker, mode="yoy", kind="fy", period_end=None):
    index = index_facts(companyfacts)
    cur, base = find_periods(index, mode, kind, period_end)
    lines, warnings = build_lines(index, cur, base)
    budget, actuals, kinds = to_bva(lines, cur, base)
    sources = []
    for l in lines:
        for role, facts in (("actual", l.cur_src), ("baseline", l.base_src)):
            for f in facts:
                sources.append(source_record(f"{l.cost_center} / {l.label}", role, f, l.derived))
    memo = memo_lines(index, cur, base)
    for m in memo:
        for role, facts in (("actual", m["cur_src"]), ("baseline", m["base_src"])):
            for f in facts:
                sources.append(source_record(f"memo / {m['label']}", role, f, m["note"]))
    company = companyfacts.get("entityName", ticker)
    through = _month(cur.end)
    word = {"fy": "year over year", "ytd": "year to date, year over year", "q": "quarter, year over year"}[kind]
    base_word = "prior year" if mode == "yoy" else "prior quarter"
    fl_cur, fl_base = fiscal_label(index, cur), fiscal_label(index, base)
    fl = f" [{fl_cur}]" if fl_cur else ""
    oi_memo = next((m for m in memo if m["key"] == "operating_income"), None)
    recon = {"ok": True}
    if oi_memo:
        sign = lambda l: 1 if l.kind == REVENUE else -1
        mc, mb = (sum(sign(l) * l.cur for l in lines), sum(sign(l) * l.base for l in lines))
        tol = max(1.0, 0.0005 * abs(lines[0].cur))
        recon = {"ok": abs(mc - oi_memo["cur"]) <= tol and abs(mb - oi_memo["base"]) <= tol,
                 "lines_cur": mc, "lines_base": mb, "reported_cur": oi_memo["cur"], "reported_base": oi_memo["base"]}
    meta = {"title": f"{company} ({ticker.upper()}): {word if mode == 'yoy' else 'quarter over quarter'}{fl}",
            "fiscal_label": fl_cur, "baseline_fiscal_label": fl_base, "reconciliation": recon,
            "baseline_word": base_word, "period_label": period_label(cur),
            "baseline_label": period_label(base), "no_projection": True,
            "note": f"SEC EDGAR data. Budget column = {base_word} ({period_label(base)}"
                    f"{', ' + fl_base if fl_base else ''}); actual = {period_label(cur)}"
                    f"{' (' + fl_cur + ')' if fl_cur else ''}",
            "chart_label": f"{ticker.upper()} {fl_cur + ' (' + cur.start + ' to ' + cur.end + ')' if fl_cur else period_label(cur)} vs {base_word}"}
    rev = next(l for l in lines if l.key == "revenue")
    return SecDataset(company, ticker.upper(), mode if mode == "qoq" else f"yoy-{kind}", cur, base, budget, actuals,
                      kinds, sources, memo, warnings, lines, through, rev.base, meta)
