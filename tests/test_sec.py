"""Public-company mode, offline. Fixtures are trimmed copies of real SEC data (see tests/fixtures).

Tie-outs are against the filings as printed:
  Home Depot 10-K for the fiscal year ended 2026-02-01, accession 0001628280-26-019436:
    Net sales 164,683 / 159,514; Cost of sales 109,818 / 106,206; SG&A 30,702 / 28,748;
    Depreciation and amortization 3,273 / 3,034; Operating income 20,890 / 21,526 ($ millions).
    Segment note: Primary 151,966 / 153,108 and Other 12,717 / 6,406 net sales.
  Microsoft 10-K for the fiscal year ended 2026-06-30, accession 0001193125-26-323660:
    Revenue 331,839 / 281,724; segment revenue Productivity and Business Processes 139,996,
    Intelligent Cloud 137,791, More Personal Computing 54,052; segment operating income 83,879 / 56,972 / 14,386.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import cli
from bva.guidance import evaluate
from bva.ixbrl import IxbrlError, extract_segments, load_instance, parse_xml
from bva.secdata import SecError, build_dataset, cik_for_ticker, load_json

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
HD_ACC, MSFT_ACC = "0001628280-26-019436", "0001193125-26-323660"
M = 1e6


def fx(name):
    return os.path.join(FIX, name)


def lines(ds):
    return {l.label: l for l in ds.lines}


class YoYTieOut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = build_dataset(load_json(fx("hd_companyfacts.json")), "HD", "yoy", "fy")

    def test_periods(self):
        self.assertEqual((self.ds.cur.start, self.ds.cur.end), ("2025-02-03", "2026-02-01"))
        self.assertEqual((self.ds.base.start, self.ds.base.end), ("2024-01-29", "2025-02-02"))

    def test_income_statement_as_printed(self):
        l = lines(self.ds)
        for label, cur, base in (("Revenue", 164683, 159514), ("Cost of revenue", 109818, 106206),
                                 ("Selling, general and administrative", 30702, 28748),
                                 ("Depreciation and amortization", 3273, 3034)):
            self.assertEqual(l[label].cur, cur * M, label)
            self.assertEqual(l[label].base, base * M, label)
        memo = {m["key"]: m for m in self.ds.memo}
        self.assertEqual((memo["operating_income"]["cur"], memo["operating_income"]["base"]), (20890 * M, 21526 * M))

    def test_lines_reconcile_to_reported_operating_income(self):
        self.assertEqual(self.ds.warnings, [])

    def test_every_figure_has_a_source(self):
        self.assertTrue(self.ds.sources)
        for s in self.ds.sources:
            self.assertEqual(s["accession"], HD_ACC)
            self.assertEqual(s["form"], "10-K")
            self.assertTrue(s["tag"].startswith("us-gaap:"))
            self.assertTrue(s["period_start"] and s["period_end"])

    def test_engine_signs(self):
        an = cli.analyze(self.ds.budget, self.ds.actuals, self.ds.through, kinds=self.ds.kinds, method="budget-remaining")
        rev = next(r for r in an.lines if r.category == "Revenue")
        self.assertEqual(rev.var, (164683 - 159514) * M)
        self.assertEqual(rev.status, "F")          # revenue above baseline is favorable
        cost = next(r for r in an.lines if r.category == "Cost of revenue")
        self.assertEqual(cost.status, "U")         # expense above baseline is unfavorable
        self.assertEqual(an.operating_income.actual, 20890 * M)
        self.assertEqual(an.operating_income.budget, 21526 * M)


class Quarterly(unittest.TestCase):
    def setUp(self):
        self.facts = load_json(fx("hd_companyfacts.json"))

    def test_quarter_yoy(self):
        ds = build_dataset(self.facts, "HD", "yoy", "q")
        l = lines(ds)
        self.assertEqual((l["Revenue"].cur, l["Revenue"].base), (47861 * M, 45277 * M))
        self.assertEqual({s["accession"] for s in ds.sources}, {"0001628280-26-058715"})

    def test_ytd_yoy(self):
        l = lines(build_dataset(self.facts, "HD", "yoy", "ytd"))
        self.assertEqual((l["Revenue"].cur, l["Revenue"].base), (89626 * M, 85133 * M))

    def test_qoq(self):
        l = lines(build_dataset(self.facts, "HD", "qoq"))
        self.assertEqual((l["Revenue"].cur, l["Revenue"].base), (47861 * M, 41765 * M))

    def test_qoq_derives_fourth_quarter_and_says_so(self):
        ds = build_dataset(self.facts, "HD", "qoq", "q", "2026-05-03")
        self.assertTrue(ds.base.derived)
        l = lines(ds)
        self.assertEqual(l["Revenue"].base, (164683 - 126485) * M)   # FY minus nine months
        self.assertTrue(any("derived" in s["note"] for s in ds.sources))

    def test_other_company_mapping(self):
        ds = build_dataset(load_json(fx("msft_companyfacts.json")), "MSFT", "yoy", "fy")
        l = lines(ds)
        self.assertEqual((l["Revenue"].cur, l["Revenue"].base), (331839 * M, 281724 * M))
        self.assertEqual(ds.warnings, [])

    def test_unknown_period_end(self):
        with self.assertRaises(SecError):
            build_dataset(self.facts, "HD", "yoy", "fy", "1999-01-01")

    def test_ticker_lookup(self):
        t = load_json(fx("company_tickers.json"))
        self.assertEqual(cik_for_ticker("hd", t), 354950)
        with self.assertRaises(SecError):
            cik_for_ticker("NOPE", t)


class Segments(unittest.TestCase):
    def test_msft_segments_as_printed(self):
        res = extract_segments(load_instance(fx("msft_10k_fy2026_trimmed.htm")))
        self.assertEqual((res.cur_end, res.base_end), ("2026-06-30", "2025-06-30"))
        rows = {r.member.split(":")[1]: r for r in res.rows}
        self.assertEqual(rows["ProductivityAndBusinessProcessesMember"].revenue_cur, 139996 * M)
        self.assertEqual(rows["IntelligentCloudMember"].revenue_cur, 137791 * M)
        self.assertEqual(rows["MorePersonalComputingMember"].revenue_cur, 54052 * M)
        self.assertEqual(rows["ProductivityAndBusinessProcessesMember"].oi_cur, 83879 * M)
        self.assertEqual(rows["IntelligentCloudMember"].oi_cur, 56972 * M)
        self.assertEqual(rows["MorePersonalComputingMember"].oi_cur, 14386 * M)
        self.assertEqual(sum(r.revenue_cur for r in res.rows), 331839 * M)
        self.assertEqual(res.warnings, [])

    def test_hd_segments_are_flagged_not_guessed(self):
        res = extract_segments(load_instance(fx("hd_10k_fy2025_trimmed.htm")))
        rows = {r.label: r for r in res.rows}
        self.assertEqual(rows["Primary Segment"].revenue_cur, 151966 * M)
        self.assertEqual(rows["Primary Segment"].revenue_base, 153108 * M)
        self.assertEqual(rows["All Other Segments"].revenue_cur, 12717 * M)
        self.assertEqual(rows["All Other Segments"].revenue_base, 6406 * M)
        self.assertEqual(rows["Primary Segment"].oi_cur, 20574 * M)
        self.assertTrue(any("generic member name" in w for w in res.warnings))
        self.assertEqual(res.consolidated_revenue[0], 164683 * M)

    def test_other_axes_and_missing_axis(self):
        res = extract_segments(load_instance(fx("hd_10k_fy2025_trimmed.htm")), "geography")
        us = next(r for r in res.rows if r.member == "country:US")
        self.assertEqual(us.revenue_cur, 152170 * M)
        with self.assertRaises(IxbrlError) as cm:
            extract_segments(parse_xml(XML_NO_SEGMENTS))
        self.assertIn("single-axis cuts found", str(cm.exception))

    def test_xml_instance_and_sum_mismatch_flag(self):
        res = extract_segments(parse_xml(XML_TWO_SEGMENTS))
        self.assertEqual({r.label: r.revenue_cur for r in res.rows}, {"Alpha": 600.0, "Beta": 300.0})
        self.assertTrue(any("sum to" in w for w in res.warnings))
        self.assertTrue(any("no operating income" in w for w in res.warnings))

    def test_cli_segments_outputs_carry_accession(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["sec", "--mode", "segments", "--instance", fx("msft_10k_fy2026_trimmed.htm"),
                               "--accession", MSFT_ACC, "--csv", os.path.join(d, "o.csv"),
                               "--html", os.path.join(d, "o.html"), "--xlsx", os.path.join(d, "o.xlsx"),
                               "--charts", os.path.join(d, "c")])
            self.assertEqual(rc, 0)
            self.assertIn("Intelligent Cloud", buf.getvalue())
            self.assertIn(MSFT_ACC, buf.getvalue())
            with open(os.path.join(d, "o.sources.csv")) as f:
                src = f.read()
            self.assertIn(MSFT_ACC, src)
            self.assertIn("msft:IntelligentCloudMember", src)
            html = open(os.path.join(d, "o.html")).read()
            self.assertIn(MSFT_ACC, html)
            self.assertNotIn("Synthetic", html)
            for name in os.listdir(os.path.join(d, "c")):
                self.assertNotIn("Synthetic", open(os.path.join(d, "c", name)).read())


class Guidance(unittest.TestCase):
    def test_example_file(self):
        rows = evaluate(os.path.join(os.path.dirname(__file__), "..", "data", "guidance_HD_example.csv"),
                        load_json(fx("hd_companyfacts.json")))
        by = {r.metric: r for r in rows}
        self.assertAlmostEqual(by["revenue_growth_pct"].actual, (164683 / 159514 - 1) * 100, 6)
        self.assertEqual(by["revenue_growth_pct"].position, "above")
        self.assertEqual(by["operating_margin_pct"].position, "below")
        self.assertEqual(by["diluted_eps"].actual, 14.23)
        for r in rows:
            self.assertIn("0000354950-25-000030", r.source)

    def test_template_is_empty_and_missing_source_fails(self):
        here = os.path.join(os.path.dirname(__file__), "..", "data", "guidance_template.csv")
        self.assertEqual(evaluate(here, load_json(fx("hd_companyfacts.json"))), [])
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("metric,period_end,duration,low,high,point,unit,source\nrevenue,2026-02-01,FY,1,2,,usd,\n")
        try:
            with self.assertRaises(SecError):
                evaluate(f.name, load_json(fx("hd_companyfacts.json")))
        finally:
            os.unlink(f.name)

    def test_dollar_guidance_in_engine(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("metric,period_end,duration,low,high,point,unit,source\n"
                    "revenue,2026-02-01,FY,150,160,,billions,test row only\n")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["sec", "--ticker", "HD", "--mode", "guidance", "--guidance", f.name,
                               "--facts", fx("hd_companyfacts.json")])
            self.assertEqual(rc, 0)
            self.assertIn("above", buf.getvalue())
            self.assertIn("+9,683,000,000", buf.getvalue())   # 164,683 actual vs 155,000 midpoint, in $
        finally:
            os.unlink(f.name)


class Cli(unittest.TestCase):
    def test_yoy_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["sec", "--ticker", "HD", "--mode", "yoy", "--facts", fx("hd_companyfacts.json"),
                               "--csv", os.path.join(d, "o.csv"), "--html", os.path.join(d, "o.html"),
                               "--xlsx", os.path.join(d, "o.xlsx")])
            self.assertEqual(rc, 0)
            self.assertIn("164,683,000,000", buf.getvalue())
            self.assertIn(HD_ACC, open(os.path.join(d, "o.html")).read())
            self.assertIn(HD_ACC, open(os.path.join(d, "o.sources.csv")).read())
            self.assertTrue(os.path.getsize(os.path.join(d, "o.xlsx")) > 0)

    def test_segments_needs_instance(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            cli.main(["sec", "--mode", "segments"])


_HEAD = ('<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
         'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
         'xmlns:ex="http://example.com/ex">')


def _ctx(cid, member=None, axis="us-gaap:StatementBusinessSegmentsAxis", start="2025-01-01", end="2025-12-31"):
    seg = (f'<xbrli:segment><xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember></xbrli:segment>'
           if member else "")
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>{seg}'
            f'</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>'
            f'</xbrli:period></xbrli:context>')


def _fact(ctx, val, tag="us-gaap:Revenues"):
    return f'<{tag} contextRef="{ctx}" unitRef="usd" decimals="0">{val}</{tag}>'


# Two segments of 600 and 300 against a consolidated 1,000, and Beta has no operating income.
XML_TWO_SEGMENTS = (_HEAD + _ctx("all") + _ctx("a", "ex:AlphaMember") + _ctx("b", "ex:BetaMember")
                    + _ctx("all0", start="2024-01-01", end="2024-12-31")
                    + _ctx("a0", "ex:AlphaMember", start="2024-01-01", end="2024-12-31")
                    + _ctx("b0", "ex:BetaMember", start="2024-01-01", end="2024-12-31")
                    + _fact("all", 1000) + _fact("a", 600) + _fact("b", 300)
                    + _fact("all0", 800) + _fact("a0", 500) + _fact("b0", 250)
                    + _fact("a", 100, "us-gaap:OperatingIncomeLoss") + _fact("a0", 90, "us-gaap:OperatingIncomeLoss")
                    + "</xbrli:xbrl>")
# Only a geography cut: asking for segments must explain what was found, not guess.
XML_NO_SEGMENTS = (_HEAD + _ctx("us", "country:US", "srt:StatementGeographicalAxis")
                   + _ctx("us0", "country:US", "srt:StatementGeographicalAxis", "2024-01-01", "2024-12-31")
                   + _fact("us", 5) + _fact("us0", 4) + "</xbrli:xbrl>")


if __name__ == "__main__":
    unittest.main()


# ---- audit fixes -----------------------------------------------------------------------------------

def _cf(values, fy=(2025, 2024)):
    """Minimal companyfacts: values is {tag: (current, prior)} for a calendar year, filed in one 10-K."""
    facts = {}
    for tag, (cur, base) in values.items():
        unit = "USD/shares" if "PerShare" in tag else "USD"
        facts[tag] = {"units": {unit: [
            {"start": "2025-01-01", "end": "2025-12-31", "val": cur, "accn": "0000000000-26-000001", "form": "10-K",
             "filed": "2026-02-01", "fy": fy[0], "fp": "FY"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": base, "accn": "0000000000-26-000001", "form": "10-K",
             "filed": "2026-02-01", "fy": fy[0], "fp": "FY"}]}}
    return {"entityName": "Test Co", "facts": {"us-gaap": facts}}


class RevenueTagChoice(unittest.TestCase):
    TAGS = {"RevenueFromContractWithCustomerExcludingAssessedTax": (247, 240), "Revenues": (371, 350),
            "CostOfRevenue": (100, 90), "OperatingIncomeLoss": (271, 260)}

    def test_larger_total_wins_and_gap_is_flagged(self):
        ds = build_dataset(_cf(self.TAGS), "TST", "yoy", "fy")
        rev = lines(ds)["Revenue"]
        self.assertEqual((rev.cur, rev.base, rev.tag), (371, 350, "Revenues"))
        self.assertTrue(any("revenue tags disagree" in w and "371" in w and "247" in w for w in ds.warnings))

    def test_same_total_gives_no_warning(self):
        t = dict(self.TAGS, Revenues=(247, 240), OperatingIncomeLoss=(147, 150))
        ds = build_dataset(_cf(t), "TST", "yoy", "fy")
        self.assertFalse(any("revenue tags" in w for w in ds.warnings))

    def test_guidance_revenue_uses_larger_tag(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("metric,period_end,duration,low,high,point,unit,source\nrevenue,2025-12-31,FY,300,400,,usd,test\n")
        try:
            row = evaluate(f.name, _cf(self.TAGS))[0]
            self.assertEqual(row.actual, 371)
        finally:
            os.unlink(f.name)


class FiscalLabel(unittest.TestCase):
    def test_hd_uses_the_companys_fiscal_year(self):
        ds = build_dataset(load_json(fx("hd_companyfacts.json")), "HD", "yoy", "fy")
        self.assertEqual(ds.meta["fiscal_label"], "fiscal 2025")      # period ends 2026-02-01
        self.assertEqual(ds.meta["baseline_fiscal_label"], "fiscal 2024")
        self.assertIn("[fiscal 2025]", ds.meta["title"])


class Unreconciled(unittest.TestCase):
    T = {"Revenues": (1000, 900), "CostOfRevenue": (400, 380), "OperatingIncomeLoss": (350, 300)}   # lines add to 600 / 520

    def _run(self):
        buf = io.StringIO()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(_cf(self.T), f)
        try:
            with redirect_stdout(buf):
                cli.main(["sec", "--ticker", "TST", "--facts", f.name, "--no-commentary"])
        finally:
            os.unlink(f.name)
        return buf.getvalue()

    def test_reports_reported_income_and_the_gap(self):
        out = self._run()
        self.assertIn("Operating income as reported: baseline 300, actual 350", out)
        self.assertIn("Unreconciled gap", out)
        self.assertIn("+220", out)       # 520 - 300 baseline gap
        self.assertNotIn("Operating income (revenue less expense lines)", out)

    def test_reconciled_company_keeps_the_engine_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["sec", "--ticker", "HD", "--facts", fx("hd_companyfacts.json"), "--no-commentary"])
        self.assertIn("Operating income (revenue less expense lines)", buf.getvalue())

    def test_top_drivers_columns_line_up(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["sec", "--ticker", "HD", "--facts", fx("hd_companyfacts.json"), "--no-commentary"])
        rows = [l for l in buf.getvalue().split("\n") if l[:3].strip().rstrip(".").isdigit() and " / " in l]
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(len({l.index("%") for l in rows}), 1)


class GuidanceDetails(unittest.TestCase):
    def _eval(self, row, facts):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("metric,period_end,duration,low,high,point,unit,source\n" + row + "\n")
        try:
            return evaluate(f.name, facts)[0]
        finally:
            os.unlink(f.name)

    def test_point_guidance_can_be_within(self):
        hd = load_json(fx("hd_companyfacts.json"))
        self.assertEqual(self._eval("diluted_eps,2026-02-01,FY,,,14.20,usd_per_share,test", hd).position, "within")   # actual 14.23
        self.assertEqual(self._eval("diluted_eps,2026-02-01,FY,,,14.60,usd_per_share,test", hd).position, "below")
        self.assertEqual(self._eval("operating_margin_pct,2026-02-01,FY,,,12.7,pct,test", hd).position, "within")     # actual 12.68

    def test_range_guidance_is_still_strict(self):
        hd = load_json(fx("hd_companyfacts.json"))
        self.assertEqual(self._eval("diluted_eps,2026-02-01,FY,14.24,14.50,,usd_per_share,test", hd).position, "below")

    def test_eps_only_guidance_does_not_print_zero_materiality(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["sec", "--ticker", "HD", "--mode", "guidance", "--facts", fx("hd_companyfacts.json"),
                      "--guidance", os.path.join(os.path.dirname(__file__), "..", "data", "guidance_HD_example.csv")])
        self.assertNotIn("materiality over $0", buf.getvalue())
        self.assertIn("no dollar threshold", buf.getvalue())


class SecCharts(unittest.TestCase):
    def test_segments_chart_and_doc_visuals(self):
        from bva.charts import build_sec_docs
        with tempfile.TemporaryDirectory() as d:
            names = build_sec_docs(d)
            self.assertEqual(len(names), 8)
            seg = open(os.path.join(d, "sec-msft-segments.svg")).read()
            self.assertIn("Intelligent Cloud", seg)
            self.assertNotIn("Synthetic", seg)
            self.assertNotIn("/Users/", open(os.path.join(d, "sec-hd-guidance.svg")).read())

    def test_charts_flag_writes_segments_chart(self):
        with tempfile.TemporaryDirectory() as d:
            with redirect_stdout(io.StringIO()):
                cli.main(["sec", "--mode", "segments", "--instance", fx("msft_10k_fy2026_trimmed.htm"),
                          "--accession", MSFT_ACC, "--charts", d, "--no-commentary"])
            self.assertIn("segments.svg", os.listdir(d))
