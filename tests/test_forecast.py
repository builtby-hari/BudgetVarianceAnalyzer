import contextlib
import csv
import io
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import cli
from bva import (REVENUE, Thresholds, analyze, forecast_analysis, forecast_commentary, load_dataset,
                 load_forecast)
from bva.charts import THEMES, forecast_accuracy_svg, outlook_svg
from bva.report import build_html
from tests.test_bva import FY, ROOT, build, tmp_csv

TH = Thresholds(50, 0.05)
HEAD = "cost_center,category,month,amount,as_of\n"


def fc_rows(cc, cat, as_of, first, value):
    """Forecast rows for months first..12 at one value."""
    return {(cc, cat, FY[i], as_of): value for i in range(first - 1, 12)}


class TestForecast(unittest.TestCase):
    def setUp(self):
        # one expense line: budget 100 a month, actual 110 for Jan-Jun
        self.b, self.a = build([("A", "x", [100] * 12)], [("A", "x", [110] * 6)])
        self.an = analyze(self.b, self.a, thresholds=TH)
        self.fc = {**fc_rows("A", "x", "2026-02", 3, 105), **fc_rows("A", "x", "2026-04", 5, 120)}

    def run_fc(self, fc=None, **kw):
        return forecast_analysis(self.an, self.b, self.a, self.fc if fc is None else fc, **kw)

    def test_three_way_variance_and_outlook(self):
        r = self.run_fc().rows[0]
        # prior forecast per closed month: Mar, Apr use the Feb version (105); May, Jun the Apr version (120)
        self.assertEqual((r.cmp_actual, r.cmp_forecast, r.cmp_var), (440, 450, -10))
        # open months use the latest version made by the close (Apr, 120)
        self.assertEqual((r.open_budget, r.open_forecast, r.open_var), (600, 720, 120))
        self.assertEqual((r.outlook, r.fy_budget, r.outlook_var), (1380, 1200, 180))
        self.assertAlmostEqual(r.outlook_pct, 0.15)
        self.assertEqual((r.status, r.material), ("U", True))

    def test_accuracy_mape_and_bias(self):
        (a,) = self.run_fc().accuracy
        self.assertEqual(a.months, 4)   # Jan and Feb had no prior forecast
        self.assertAlmostEqual(a.mape, (5 + 5 + 10 + 10) / 4 / 110)
        self.assertEqual(a.bias, 10)    # over-forecast: forecast minus actual
        self.assertAlmostEqual(a.bias_pct, 10 / 440)

    def test_pooled_accuracy_and_horizon(self):
        fa = self.run_fc()
        (t,) = fa.accuracy_totals
        self.assertEqual((t.kind, t.horizon, t.n), ("expense", None, 4))
        self.assertAlmostEqual(t.wmape, 30 / 440)       # sum |error| / sum |actual|
        self.assertEqual(t.bias, 10)
        self.assertAlmostEqual(t.bias_pct, 10 / 440)
        # Mar, May are 1 month after their version's as_of; Apr, Jun are 2
        self.assertEqual([(h.horizon, h.n, h.bias) for h in fa.accuracy_by_horizon], [(1, 2, 5), (2, 2, 5)])
        self.assertAlmostEqual(fa.accuracy_by_horizon[0].wmape, 15 / 220)

    def test_pooled_mape_weights_by_actual_and_splits_kinds(self):
        b, a = build([("A", "big", [100] * 12), ("A", "small", [10] * 12), ("R", "sales", [100] * 12)],
                     [("A", "big", [1000] * 3), ("A", "small", [10] * 3), ("R", "sales", [100] * 3)])
        an = analyze(b, a, kinds={("R", "sales"): REVENUE}, thresholds=TH)
        fc = {**fc_rows("A", "big", "2026-01", 2, 900), **fc_rows("A", "small", "2026-01", 2, 20),
              **fc_rows("R", "sales", "2026-01", 2, 100)}
        fa = forecast_analysis(an, b, a, fc)
        exp, rev = fa.accuracy_totals
        self.assertAlmostEqual(exp.wmape, (2 * 100 + 2 * 10) / (2 * 1000 + 2 * 10))   # big line dominates
        self.assertAlmostEqual(next(x.mape for x in fa.accuracy if x.category == "small"), 1.0)   # unweighted view differs
        self.assertEqual((rev.kind, rev.wmape, rev.bias, rev.bias_pct), ("revenue", 0.0, 0, 0))

    def test_pooled_accuracy_edge_cases(self):
        self.assertEqual(self.run_fc({k: v for k, v in self.fc.items() if k[3] == "2026-04"}).accuracy_totals[0].n, 2)
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [0, 0, 0])])
        t = forecast_analysis(analyze(b, a, thresholds=TH), b, a, fc_rows("A", "x", "2026-01", 2, 10)).accuracy_totals[0]
        self.assertIsNone(t.wmape)      # all actuals zero: no divide by zero
        self.assertIsNone(t.bias_pct)

    def test_zero_actual_months_skip_mape(self):
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [0, 0, 0])])
        an = analyze(b, a, thresholds=TH)
        fc = fc_rows("A", "x", "2026-01", 2, 10)
        acc = forecast_analysis(an, b, a, fc).accuracy[0]
        self.assertIsNone(acc.mape)
        self.assertEqual(acc.bias, 20)
        self.assertIsNone(acc.bias_pct)

    def test_revenue_signs_and_totals(self):
        b, a = build([("A", "x", [100] * 12), ("R", "sales", [100] * 12)],
                     [("A", "x", [110] * 6), ("R", "sales", [90] * 6)])
        an = analyze(b, a, kinds={("R", "sales"): REVENUE}, thresholds=TH)
        fc = {**fc_rows("A", "x", "2026-06", 7, 110), **fc_rows("R", "sales", "2026-06", 7, 90)}
        fa = forecast_analysis(an, b, a, fc)
        rev = next(r for r in fa.rows if r.cost_center == "R")
        self.assertEqual((rev.outlook_var, rev.status), (-120, "U"))   # revenue below budget is unfavorable
        exp, rv = fa.totals
        self.assertEqual((exp.cost_center, exp.outlook, rv.cost_center, rv.outlook), ("Total Expense", 1320, "Total Revenue", 1080))

    def test_missing_forecast_for_open_month_fails_closed(self):
        fc = {k: v for k, v in self.fc.items() if k[2] != "2026-09"}
        with self.assertRaisesRegex(ValueError, "A / x 2026-09"):
            self.run_fc(fc)

    def test_versions_after_the_close_are_ignored(self):
        fc = {**self.fc, **fc_rows("A", "x", "2026-07", 8, 999)}
        fa = self.run_fc(fc)
        self.assertEqual(fa.rows[0].open_forecast, 720)
        self.assertIn("ignored", fa.notes[0])

    def test_unversioned_forecast_has_outlook_but_no_accuracy(self):
        fa = self.run_fc({(c, k, m, None): v for (c, k, m, _), v in fc_rows("A", "x", "x", 7, 100).items()})
        self.assertEqual(fa.rows[0].open_forecast, 600)
        self.assertEqual(fa.accuracy, [])

    def test_fully_closed_year_needs_no_forecast(self):
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [100] * 12)])
        fa = forecast_analysis(analyze(b, a), b, a, {})
        self.assertEqual((fa.rows[0].outlook, fa.rows[0].outlook_var), (1200, 0))


class TestForecastLoader(unittest.TestCase):
    def test_load_versioned_and_sum_duplicates(self):
        p = tmp_csv(self, HEAD + "A,x,2026-04,\"1,000\",2026-03\nA,x,2026-04,50,2026-03\nA,x,2026-04,70,2026-02\n")
        self.assertEqual(load_forecast(p), {("A", "x", "2026-04", "2026-03"): 1050.0, ("A", "x", "2026-04", "2026-02"): 70.0})

    def test_as_of_column_is_optional(self):
        p = tmp_csv(self, "cost_center,category,month,amount\nA,x,2026-04,5\n")
        self.assertEqual(load_forecast(p), {("A", "x", "2026-04", None): 5.0})

    def test_as_of_must_precede_month_and_be_valid(self):
        for row in ("A,x,2026-04,5,2026-04", "A,x,2026-04,5,2026-05", "A,x,2026-04,5,Mar", "A,x,2026-04,5,"):
            with self.assertRaises(ValueError, msg=row):
                load_forecast(tmp_csv(self, HEAD + row + "\n"))

    def test_sample_file_covers_every_open_month(self):
        data = os.path.join(ROOT, "data")
        b, a, kinds = load_dataset(os.path.join(data, "budget.csv"), os.path.join(data, "actuals.csv"))
        fa = forecast_analysis(analyze(b, a, kinds=kinds), b, a, load_forecast(os.path.join(data, "forecast.csv"), kinds))
        self.assertEqual(len(fa.rows), 24)
        self.assertEqual(len(fa.accuracy), 24)
        exp = fa.totals[0]
        self.assertAlmostEqual(exp.outlook, exp.ytd_actual + exp.open_forecast)


class TestForecastCommentary(unittest.TestCase):
    def test_one_line_per_material_line_with_both_comparisons(self):
        b, a = build([("A", "x", [100] * 12), ("A", "small", [100] * 12), ("R", "sales", [100] * 12)],
                     [("A", "x", [110] * 6), ("A", "small", [101] * 6), ("R", "sales", [90] * 6)])
        an = analyze(b, a, kinds={("R", "sales"): REVENUE}, thresholds=TH)
        fc = {}
        for cc, cat, v in (("A", "x", 120), ("A", "small", 101), ("R", "sales", 90)):
            fc.update(fc_rows(cc, cat, "2026-02", 3, v))
        lines = forecast_commentary(forecast_analysis(an, b, a, fc))
        self.assertEqual(len(lines), 2)   # A / small is under both tests, so no draft
        x = next(l for l in lines if l.startswith("A / x"))
        self.assertIn("over budget, unfavorable", x)
        self.assertIn("under forecast, favorable", x)   # actual 110 vs forecast 120
        self.assertIn("full-year outlook", x)
        rev = next(l for l in lines if l.startswith("R / sales"))
        self.assertIn("below budget, unfavorable", rev)

    def test_no_prior_forecast_is_stated(self):
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [110] * 6)])
        an = analyze(b, a, thresholds=TH)
        fa = forecast_analysis(an, b, a, {(c, k, m, None): 110 for (c, k, m) in b if m > "2026-06"})
        self.assertIn("no prior forecast to compare", forecast_commentary(fa)[0])


class TestForecastOutputs(unittest.TestCase):
    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            return cli.main(list(args)), buf.getvalue()

    def test_cli_prints_outlook_accuracy_and_drafts(self):
        code, out = self.run_cli("--forecast", os.path.join(ROOT, "data", "forecast.csv"))
        self.assertEqual(code, 0)
        for text in ("Rolling forecast", "Total Expense", "Forecast accuracy", "Forecast commentary drafts"):
            self.assertIn(text, out)

    def test_cli_csv_and_html(self):
        with tempfile.TemporaryDirectory() as d:
            paths = {k: os.path.join(d, k) for k in ("f.csv", "acc.csv", "r.html")}
            self.run_cli("--forecast", os.path.join(ROOT, "data", "forecast.csv"), "--forecast-csv", paths["f.csv"],
                         "--accuracy-csv", paths["acc.csv"], "--html", paths["r.html"])
            with open(paths["f.csv"], newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["level"] for r in rows].count("line"), 24)
            self.assertEqual(rows[-1]["cost_center"], "Total Expense")
            self.assertAlmostEqual(float(rows[-1]["outlook"]), float(rows[-1]["ytd_actual"]) + float(rows[-1]["open_forecast"]), 1)
            with open(paths["acc.csv"], newline="") as f:
                acc = list(csv.DictReader(f))
            self.assertEqual(len(acc), 24)
            self.assertEqual(list(acc[0]), ["cost_center", "category", "type", "months", "mape", "bias", "bias_pct"])
            with open(paths["r.html"]) as f:
                html = f.read()
            for text in ("Rolling Forecast", "Forecast Accuracy", "Commentary Drafts"):
                self.assertIn(text, html)

    def test_html_has_no_forecast_section_without_a_forecast(self):
        b, a, k = load_dataset(os.path.join(ROOT, "data", "budget.csv"), os.path.join(ROOT, "data", "actuals.csv"))
        self.assertNotIn("Rolling Forecast", build_html(analyze(b, a, kinds=k)))

    def test_cli_errors_are_clean(self):
        bad = tmp_csv(self, HEAD + "Technology,Contractors,2026-10,5,2026-03\n")   # open months missing for every other line
        for args in (["--forecast-csv", "x.csv"], ["--forecast", bad]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                cli.main(args)
            self.assertEqual(cm.exception.code, 2)


class TestForecastCharts(unittest.TestCase):
    def setUp(self):
        b, a = build([("A", "x", [100] * 12), ("A", "y", [50] * 12)], [("A", "x", [110] * 6), ("A", "y", [50] * 6)])
        self.an = analyze(b, a, thresholds=TH)
        self.fa = forecast_analysis(self.an, b, a, {**fc_rows("A", "x", "2026-02", 3, 105), **fc_rows("A", "y", "2026-02", 3, 50)})

    def test_both_palettes_parse_and_differ(self):
        for chart in (lambda d: outlook_svg(self.an, self.fa, dark=d), lambda d: forecast_accuracy_svg(self.fa, d)):
            light, dark = chart(False), chart(True)
            ET.fromstring(light)
            ET.fromstring(dark)
            self.assertIn(THEMES["light"]["bg"], light)
            self.assertNotIn('fill="#ffffff"', dark)
            self.assertIn(THEMES["dark"]["navy"], dark)

    def test_charts_name_the_lines_and_pooled_mape(self):
        self.assertIn("A: x", outlook_svg(self.an, self.fa))
        self.assertIn("A / x", forecast_accuracy_svg(self.fa))
        self.assertIn("expense 3.1%", forecast_accuracy_svg(self.fa))   # 20 / (4 x 110 + 4 x 50), weighted by actual

    def test_zero_data_is_safe(self):
        b, a = build([("A", "x", [0] * 12)], [("A", "x", [0] * 6)])
        an = analyze(b, a, thresholds=TH)
        fa = forecast_analysis(an, b, a, {**fc_rows("A", "x", "2026-02", 3, 0)})
        for dark in (False, True):
            for svg in (outlook_svg(an, fa, dark=dark), forecast_accuracy_svg(fa, dark)):
                ET.fromstring(svg)
                self.assertNotIn("nan", svg.lower().replace("financ", ""))
                self.assertNotIn("inf", svg)

    def test_no_accuracy_and_no_expense_lines_are_safe(self):
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [110] * 6)])
        fa = forecast_analysis(analyze(b, a, thresholds=TH), b, a, fc_rows("A", "x", "2026-06", 7, 105))   # only a version made at the close: nothing to score
        self.assertEqual(fa.accuracy, [])
        self.assertIn("No closed month", forecast_accuracy_svg(fa))
        ET.fromstring(forecast_accuracy_svg(fa, True))
        fa.totals, fa.rows = [], []   # revenue-only shape: nothing to bridge
        ET.fromstring(outlook_svg(self.an, fa))

    def test_report_includes_forecast_charts(self):
        self.an.forecast = self.fa
        html = build_html(self.an)
        self.assertIn("Full-Year Outlook by Driver", html)
        self.assertIn("Forecast Accuracy by Line", html)
        self.assertEqual(html.count("<svg"), 4)   # waterfall, monthly, outlook, accuracy


if __name__ == "__main__":
    unittest.main()
