"""Segment extraction from a saved inline-XBRL 10-K (.htm) or an XBRL instance (.xml).

The SEC companyfacts API has no dimensional data, so segment revenue and operating income have to
come from the filing itself: contexts that carry one explicit member on a segment axis. This parser
reads those and maps each member into a BVA cost center. Standard library only.

It flags instead of guessing: generic member names, members whose segments do not add up to the
consolidated figure, conflicting duplicate facts, multi-axis contexts it skipped, and segments
that have revenue but no tagged operating income.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from typing import Dict, List, Optional, Tuple

from .variance import EXPENSE, REVENUE

AXES = {"segment": "us-gaap:StatementBusinessSegmentsAxis",
        "product": "srt:ProductOrServiceAxis",
        "geography": "srt:StatementGeographicalAxis"}
REVENUE_TAGS = ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "us-gaap:Revenues",
                "us-gaap:SalesRevenueNet"]
OI_TAG = "us-gaap:OperatingIncomeLoss"
GENERIC_MEMBERS = {"AllOtherSegmentsMember", "PrimarySegmentMember", "CorporateNonSegmentMember",
                   "IntersegmentEliminationMember", "SegmentReconcilingItemsMember", "OtherSegmentsMember",
                   "CorporateAndOtherMember", "NonUsMember", "ProductMember", "ServiceMember"}


class IxbrlError(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    id: str
    dims: Tuple[Tuple[str, str], ...]
    start: Optional[str]
    end: Optional[str]
    instant: Optional[str]
    typed: bool = False


@dataclass(frozen=True)
class IxFact:
    name: str
    ctx: str
    value: Optional[float]     # in whole dollars (scale applied), None for a nil fact
    raw: str
    scale: int
    decimals: Optional[str]


@dataclass
class Instance:
    contexts: Dict[str, Context] = field(default_factory=dict)
    facts: List[IxFact] = field(default_factory=list)
    dei: Dict[str, str] = field(default_factory=dict)


@dataclass
class SegmentRow:
    member: str
    label: str
    revenue_cur: Optional[float]
    revenue_base: Optional[float]
    oi_cur: Optional[float]
    oi_base: Optional[float]
    revenue_tag: str = ""
    sources: List[dict] = field(default_factory=list)


@dataclass
class SegmentResult:
    axis: str
    cur_end: str
    base_end: str
    rows: List[SegmentRow]
    consolidated_revenue: Tuple[Optional[float], Optional[float]]
    consolidated_oi: Tuple[Optional[float], Optional[float]]
    warnings: List[str]
    axes_found: Dict[str, int]
    dei: Dict[str, str]


_CTX = re.compile(r"<xbrli:context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:context>", re.S)
_EXPL = re.compile(r"<xbrldi:explicitMember\b[^>]*dimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.S)
_NONFRAC = re.compile(r"<ix:nonFraction\b([^>]*?)(?:/>|>(.*?)</ix:nonFraction>)", re.S)
_NONNUM = re.compile(r"<ix:nonNumeric\b([^>]*?)>(.*?)</ix:nonNumeric>", re.S)
_ATTR = re.compile(r"([\w:.-]+)=\"([^\"]*)\"")


def _tag(body, name):
    m = re.search(rf"<xbrli:{name}>\s*([^<\s]+)\s*<", body)
    return m.group(1) if m else None


def _number(raw, attrs):
    """Whole-dollar value from the displayed text and the scale, sign and format attributes."""
    fmt = attrs.get("format", "")
    if attrs.get("xsi:nil") == "true":
        return None
    text = unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()
    if "zerodash" in fmt or "fixed-zero" in fmt or text in ("", "-", "—", "–"):
        v = 0.0
    else:
        comma_decimal = "numcommadecimal" in fmt or "num-comma-decimal" in fmt
        t = text.replace(" ", "")
        t = t.replace(".", "").replace(",", ".") if comma_decimal else t.replace(",", "")
        try:
            v = float(t)
        except ValueError:
            return None
    v *= 10 ** int(attrs.get("scale", "0") or 0)
    return -v if attrs.get("sign") == "-" else v


def parse_htm(text):
    inst = Instance()
    for cid, body in _CTX.findall(text):
        dims = tuple(_EXPL.findall(body))
        inst.contexts[cid] = Context(cid, dims, _tag(body, "startDate"), _tag(body, "endDate"),
                                     _tag(body, "instant"), "typedMember" in body)
    for attrs_s, inner in _NONFRAC.findall(text):
        a = dict(_ATTR.findall(attrs_s))
        if "name" in a and "contextRef" in a:
            inst.facts.append(IxFact(a["name"], a["contextRef"], _number(inner, a),
                                     unescape(re.sub(r"<[^>]+>", "", inner or "")).strip(),
                                     int(a.get("scale", "0") or 0), a.get("decimals")))
    for attrs_s, inner in _NONNUM.findall(text):
        a = dict(_ATTR.findall(attrs_s))
        if a.get("name", "").startswith("dei:"):
            inst.dei.setdefault(a["name"], unescape(re.sub(r"<[^>]+>", "", inner)).strip())
    return inst


def parse_xml(text):
    inst = Instance()
    root = ET.fromstring(text)
    ns = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi"}
    for c in root.iter("{%s}context" % ns["xbrli"]):
        dims = tuple((m.get("dimension"), (m.text or "").strip())
                     for m in c.iter("{%s}explicitMember" % ns["xbrldi"]))
        g = lambda p: (c.find(f".//xbrli:{p}", ns).text.strip() if c.find(f".//xbrli:{p}", ns) is not None else None)
        inst.contexts[c.get("id")] = Context(c.get("id"), dims, g("startDate"), g("endDate"), g("instant"),
                                             c.find(".//xbrldi:typedMember", ns) is not None)
    prefixes = {}
    for ev, (p, uri) in ET.iterparse(__import__("io").StringIO(text), events=("start-ns",)):
        prefixes[uri] = p
    for el in root:
        ctx = el.get("contextRef")
        if not ctx or not el.tag.startswith("{"):
            continue
        uri, local = el.tag[1:].split("}")
        name = f"{prefixes.get(uri, uri)}:{local}"
        raw = (el.text or "").strip()
        if name.startswith("dei:"):
            inst.dei.setdefault(name, raw)
        if el.get("unitRef"):
            try:
                v = float(raw) if raw else None
            except ValueError:
                v = None
            inst.facts.append(IxFact(name, ctx, v, raw, 0, el.get("decimals")))
    return inst


def load_instance(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if path.lower().endswith(".xml") or text.lstrip().startswith("<?xml") and "<ix:" not in text[:200000]:
        return parse_xml(text)
    return parse_htm(text)


def humanize(member):
    local = member.split(":")[-1]
    local = re.sub(r"Member$", "", local)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local).strip()


def _days(s, e):
    return (date.fromisoformat(e) - date.fromisoformat(s)).days


def extract_segments(inst, axis="segment"):
    """Segment revenue and operating income per member on the chosen axis, current and prior year."""
    if axis not in AXES:
        raise IxbrlError(f"axis must be one of {', '.join(AXES)}")
    want = AXES[axis]
    warnings = []
    axes_found: Dict[str, int] = {}
    multi = 0
    # (tag, member, start, end) -> list of (value, context id)
    seg: Dict[Tuple[str, str, str, str], List[Tuple[float, str]]] = {}
    cons: Dict[Tuple[str, str, str], List[float]] = {}
    for f in inst.facts:
        c = inst.contexts.get(f.ctx)
        if c is None or not c.start or not c.end or not (340 <= _days(c.start, c.end) <= 380):
            continue
        if f.name not in REVENUE_TAGS and f.name != OI_TAG:
            continue
        if f.value is None:
            continue
        if not c.dims and not c.typed:
            cons.setdefault((f.name, c.start, c.end), []).append(f.value)
            continue
        if len(c.dims) + (1 if c.typed else 0) != 1 or c.typed:
            multi += 1
            continue
        ax, mem = c.dims[0]
        axes_found[ax] = axes_found.get(ax, 0) + 1
        if ax == want:
            seg.setdefault((f.name, mem, c.start, c.end), []).append((f.value, c.id))
    if multi:
        warnings.append(f"{multi} revenue or operating income facts on multi-axis contexts were skipped (not a single segment cut)")
    if not seg:
        found = ", ".join(f"{k} ({v})" for k, v in sorted(axes_found.items())) or "none"
        raise IxbrlError(f"no annual revenue or operating income facts on {want}; "
                         f"single-axis cuts found: {found}. Try --axis product or --axis geography")
    ends = sorted({k[3] for k in seg})
    cur_end = ends[-1]
    prior = [e for e in ends if 350 <= _days(e, cur_end) <= 380]
    if not prior:
        raise IxbrlError(f"no prior-year segment facts to compare with {cur_end}")
    base_end = prior[-1]
    start_of = {k[3]: k[2] for k in seg}
    # choose one revenue tag: the first that has segment facts for both years
    rev_tag = next((t for t in REVENUE_TAGS
                    if any(k[0] == t and k[3] == cur_end for k in seg) and any(k[0] == t and k[3] == base_end for k in seg)),
                   None)
    if rev_tag is None:
        warnings.append("no revenue tag with segment facts in both years; revenue left out")
    members = sorted({k[1] for k in seg})

    def get(tag, member, end):
        hits = [(k, v) for k, v in seg.items() if k[0] == tag and k[1] == member and k[3] == end]
        if not hits:
            return None, None
        k, vals = hits[0]
        distinct = {round(v, 2) for v, _ in vals}
        if len(distinct) > 1:
            warnings.append(f"{humanize(member)} {tag.split(':')[1]} {end}: conflicting duplicate facts {sorted(distinct)}; skipped")
            return None, None
        return vals[0][0], {"member": member, "tag": tag, "context": vals[0][1], "start": k[2], "end": end,
                            "value": vals[0][0]}

    rows = []
    for m in members:
        rc, rcs = get(rev_tag, m, cur_end) if rev_tag else (None, None)
        rb, rbs = get(rev_tag, m, base_end) if rev_tag else (None, None)
        oc, ocs = get(OI_TAG, m, cur_end)
        ob, obs = get(OI_TAG, m, base_end)
        if all(x is None for x in (rc, rb, oc, ob)):
            continue
        srcs = []
        for role, s in (("actual", rcs), ("baseline", rbs), ("actual", ocs), ("baseline", obs)):
            if s:
                srcs.append({"role": role, **s})
        rows.append(SegmentRow(m, humanize(m), rc, rb, oc, ob, rev_tag or "", srcs))
    for r in rows:
        if r.member.split(":")[-1] in GENERIC_MEMBERS:
            warnings.append(f"{r.label}: generic member name; check the filing's segment note for what it holds")
        if r.revenue_cur is not None and r.revenue_base is not None and (r.oi_cur is None or r.oi_base is None):
            warnings.append(f"{r.label}: revenue tagged but no operating income for both years; shown as revenue only")
        if (r.revenue_cur is None) != (r.revenue_base is None):
            warnings.append(f"{r.label}: revenue present for only one year; left out of the comparison")

    def consol(tag_list, end):
        for t in tag_list:
            for (name, s, e), vals in cons.items():
                if name == t and e == end:
                    return vals[0]
        return None

    c_rev = (consol([rev_tag] if rev_tag else [], cur_end), consol([rev_tag] if rev_tag else [], base_end))
    c_oi = (consol([OI_TAG], cur_end), consol([OI_TAG], base_end))
    for label, side, idx in (("revenue", c_rev, "revenue"), ("operating income", c_oi, "oi")):
        for j, end in enumerate((cur_end, base_end)):
            total = sum(getattr(r, f"{idx}_{'cur' if j == 0 else 'base'}") or 0 for r in rows)
            if side[j] is not None and abs(total - side[j]) > max(1.0, 0.0005 * abs(side[j])):
                warnings.append(f"segment {label} for {end} sum to {total:,.0f} but consolidated is {side[j]:,.0f} "
                                f"(difference {side[j] - total:+,.0f}: eliminations, unallocated items or another cut)")
    if axis == "segment" and len(rows) == 1:
        warnings.append("only one segment member found; the company may report a single segment")
    return SegmentResult(axis, cur_end, base_end, rows, c_rev, c_oi, warnings, axes_found, dict(inst.dei))
