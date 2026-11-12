"""Company guidance versus reported actuals.

Guidance is not machine readable, so the user types it into a small CSV and the reported figure
comes from XBRL. Columns:
  metric       one of the keys in secdata.METRICS (revenue, operating_income, net_income, diluted_eps, ...)
               or revenue_growth_pct / operating_margin_pct (shown in the table only, not a dollar amount)
  period_end   YYYY-MM-DD, the end of the period the guidance covers
  duration     FY, Q or YTD (default FY)
  low, high    the guided range, or leave both blank and give point
  point        a single guided number
  unit         usd (default), thousands, millions, billions, usd_per_share or pct
  source       citation, e.g. "8-K 2025-11-18 earnings release p.2". Required.
Lines starting with # are comments.

Position (below, within, above): a range is inclusive. A point guide ("approximately 2.8%") counts as within when
the actual is inside POINT_TOLERANCE of the point: 0.5% of the point for dollar and per-share metrics, 0.1
percentage point for percent metrics. A company rarely lands on a single number to the cent.
"""

import csv
from dataclasses import dataclass
from typing import List, Optional

from .secdata import LABELS, METRICS, REVENUE_TAGS, SecError, Period, index_facts, revenue_choice, value
from .variance import EXPENSE, REVENUE

SCALE = {"usd": 1.0, "usd_per_share": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9, "pct": 1.0}
POINT_TOLERANCE = 0.005      # relative, dollar and per-share point guidance
POINT_TOLERANCE_PCT = 0.1    # absolute percentage points, percent point guidance
PCT_METRICS = {"revenue_growth_pct": "Revenue growth (%)", "operating_margin_pct": "Operating margin (%)"}


@dataclass
class GuidanceRow:
    metric: str
    label: str
    duration: str
    period_end: str
    low: Optional[float]
    high: Optional[float]
    mid: float
    unit: str
    source: str
    actual: Optional[float]
    actual_sources: list
    kind: str
    in_dollar_engine: bool
    position: str            # below, within or above the guided range
    note: str = ""


def _num(raw, where):
    raw = (raw or "").strip().replace(",", "").replace("$", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise SecError(f"{where}: bad number {raw!r}") from None


def load_guidance_rows(path):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(l for l in f if not l.startswith("#"))
        need = {"metric", "period_end", "source"}
        miss = need - set(reader.fieldnames or [])
        if miss:
            raise SecError(f"{path}: missing column(s): {', '.join(sorted(miss))}")
        for n, row in enumerate(reader, 2):
            where = f"{path} row {n}"
            metric = (row.get("metric") or "").strip()
            if not metric:
                continue
            if metric not in METRICS and metric not in PCT_METRICS:
                raise SecError(f"{where}: unknown metric {metric!r}")
            source = (row.get("source") or "").strip()
            if not source:
                raise SecError(f"{where}: source citation is required")
            unit = (row.get("unit") or "usd").strip().lower()
            if unit not in SCALE:
                raise SecError(f"{where}: unit must be one of {', '.join(SCALE)}")
            low, high, point = (_num(row.get(k), where) for k in ("low", "high", "point"))
            if point is None and (low is None or high is None):
                raise SecError(f"{where}: give point, or both low and high")
            dur = (row.get("duration") or "FY").strip().upper()
            if dur not in ("FY", "Q", "YTD"):
                raise SecError(f"{where}: duration must be FY, Q or YTD")
            s = SCALE[unit]
            out.append(dict(metric=metric, duration=dur, period_end=(row.get("period_end") or "").strip(),
                            low=None if low is None else low * s, high=None if high is None else high * s,
                            point=None if point is None else point * s, unit=unit, source=source))
    return out


def evaluate(path, companyfacts):
    """GuidanceRow list. Rows with a blank template (no metric) are skipped, so a template file gives no rows."""
    index = index_facts(companyfacts)
    rows: List[GuidanceRow] = []
    for g in load_guidance_rows(path):
        lo, hi = (g["low"], g["high"]) if g["point"] is None else (g["point"], g["point"])
        mid = (lo + hi) / 2
        kind = REVENUE
        note, actual, srcs, in_engine = "", None, [], True
        if g["metric"] in PCT_METRICS:
            in_engine = False
            kind = REVENUE
            actual, srcs, note = _pct_actual(index, g)
            label = PCT_METRICS[g["metric"]]
            if g["unit"] != "pct":
                raise SecError(f"{g['metric']} needs unit pct")
        else:
            tags, kind = METRICS[g["metric"]]
            label = LABELS[g["metric"]]
            period = _period(index, tags, g)
            if period and tags is METRICS["revenue"][0]:
                # several revenue tags can exist; the larger total wins (see secdata.revenue_choice)
                cands = [(t, value(index, t, period)) for t in REVENUE_TAGS]
                cands = [(t, v) for t, v in cands if v]
                if cands:
                    best = max(cands, key=lambda tv: tv[1][0])
                    actual, srcs = best[1][0], best[1][1]
                    if len({v[0] for _, v in cands}) > 1:
                        note = f"revenue tags disagree; used the larger ({best[0]})"
            else:
                for tag in tags:
                    v = value(index, tag, period) if period else None
                    if v:
                        actual, srcs = v[0], v[1]
                        break
            if actual is None:
                note = "no reported figure found for this period yet"
        if actual is None:
            position = "no actual"
        else:
            tol = 0.0
            if g["point"] is not None:
                tol = POINT_TOLERANCE_PCT if g["unit"] == "pct" else abs(g["point"]) * POINT_TOLERANCE
            position = "below" if actual < lo - tol - 1e-9 else ("above" if actual > hi + tol + 1e-9 else "within")
        rows.append(GuidanceRow(g["metric"], label, g["duration"], g["period_end"], lo, hi, mid,
                                g["unit"], g["source"], actual, srcs, kind, in_engine, position, note))
    return rows


def _period(index, tags, g):
    for tag in tags:
        for f in index.get(tag, ()):
            if f.end == g["period_end"]:
                from .secdata import _dur_kind
                if _dur_kind(f.start, f.end) == g["duration"]:
                    return Period(g["duration"], f.start, f.end)
    return None


def _pct_actual(index, g):
    from .secdata import REVENUE_TAGS, OI_TAGS, find_periods
    tags = REVENUE_TAGS
    cur = _period(index, tags, g)
    if cur is None:
        return None, [], "no reported figure found for this period yet"
    kind = {"FY": "fy", "YTD": "ytd", "Q": "q"}[g["duration"]]
    cur2, base = find_periods(index, "yoy", kind, g["period_end"])
    if g["metric"] == "revenue_growth_pct":
        r = revenue_choice(index, cur2, base)
        if r and r[2][0]:
            return (r[1][0] / r[2][0] - 1) * 100, r[1][1] + r[2][1], "computed: this period revenue / same period last year - 1"
    else:
        r = revenue_choice(index, cur2, base)
        o = value(index, OI_TAGS[0], cur2)
        if r and o and r[1][0]:
            return o[0] / r[1][0] * 100, r[1][1] + o[1], "computed: operating income / revenue"
    return None, [], "could not compute"
