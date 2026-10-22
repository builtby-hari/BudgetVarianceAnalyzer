import csv
import os
import tempfile
import unittest

from bva import (EXPENSE, REVENUE, Thresholds, all_commentary, analyze, bridge_steps, cost_center_commentary,
                 is_material, is_wide, load_amounts, load_dataset, load_wide, run_rate_projection, status,
                 summarize_operating_income, top_drivers, variance, variance_pct)
import importlib.util
import xml.etree.ElementTree as ET

from bva.charts import (THEMES, _heat, banner_svg, bridge_svg, build_docs, cumulative_svg, flagged_svg,
                        heatmap_svg, line_items_svg, monthly_svg, sample_analyses, sample_output_svg, waterfall_svg)
from bva.report import build_html, write_csv

FY = [f"2026-{m:02d}" for m in range(1, 13)]


def series(cc, cat, values):
    return {(cc, cat, FY[i]): v for i, v in enumerate(values)}


def build(budget_specs, actual_specs):
    b, a = {}, {}
    for cc, cat, vals in budget_specs:
        b.update(series(cc, cat, vals))
    for cc, cat, vals in actual_specs:
        a.update(series(cc, cat, vals))
    return b, a


class TestVarianceBasics(unittest.TestCase):
    def test_sign_and_status(self):
        self.assertEqual(variance(120, 100), 20)        # overspend is positive
        self.assertEqual(variance(80, 100), -20)
        self.assertEqual(status(20), "U")
        self.assertEqual(status(-20), "F")
        self.assertEqual(status(0), "-")

    def test_pct(self):
        self.assertAlmostEqual(variance_pct(120, 100), 0.20)
        self.assertAlmostEqual(variance_pct(80, 100), -0.20)
        self.assertIsNone(variance_pct(50, 0))

    def test_materiality_needs_both(self):
        th = Thresholds(25_000, 0.10)
        self.assertTrue(is_material(30_000, 0.12, th))
        self.assertTrue(is_material(-30_000, -0.12, th))     # favorable can be material too
        self.assertFalse(is_material(30_000, 0.05, th))      # big $, small %
        self.assertFalse(is_material(10_000, 0.50, th))      # big %, small $
        self.assertFalse(is_material(25_000, 0.50, th))      # strictly greater than
        self.assertFalse(is_material(30_000, 0.10, th))
        self.assertTrue(is_material(30_000, None, th))       # zero budget: $ test only

    def test_run_rate(self):
        self.assertAlmostEqual(run_rate_projection([10] * 8, 4), 120)
        # last 3 months average = 20, so 4 remaining months add 80
        self.assertAlmostEqual(run_rate_projection([10, 10, 10, 10, 10, 20, 20, 20], 4, n=3), 110 + 80)
        # n=0 averages all closed months
        self.assertAlmostEqual(run_rate_projection([10, 20], 10, n=0), 30 + 150)
        self.assertAlmostEqual(run_rate_projection([10, 20], 10, n=6), 30 + 150)
        self.assertAlmostEqual(run_rate_projection([5, 5], 0), 10)    # year closed


class TestAnalysis(unittest.TestCase):
    def setUp(self):
        # Ops/Travel: budget 100/mo, actual 120/mo for 6 months. Ops/Rent: budget 50, actual 45.
        # Eng/Cloud: budget 1000/mo, actual 1100 (10% over: not material, not > 10%).
        self.b, self.a = build(
            [("Ops", "Travel", [100] * 12), ("Ops", "Rent", [50] * 12), ("Eng", "Cloud", [1000] * 12)],
            [("Ops", "Travel", [120] * 6), ("Ops", "Rent", [45] * 6), ("Eng", "Cloud", [1100] * 6)])
        self.an = analyze(self.b, self.a, thresholds=Thresholds(25, 0.10))

    def line(self, cc, cat):
        return next(r for r in self.an.lines if r.cost_center == cc and r.category == cat)

    def test_through_detected(self):
        self.assertEqual(self.an.through, "2026-06")
        self.assertEqual(self.an.months_elapsed, 6)

    def test_ytd_math_uses_only_closed_months(self):
        r = self.line("Ops", "Travel")
        self.assertEqual(r.ytd_budget, 600)            # 6 months, not 12
        self.assertEqual(r.ytd_actual, 720)
        self.assertEqual(r.var, 120)
        self.assertAlmostEqual(r.var_pct, 0.20)
        self.assertEqual(r.status, "U")
        self.assertEqual(r.fy_budget, 1200)

    def test_favorable_line(self):
        r = self.line("Ops", "Rent")
        self.assertEqual(r.var, -30)
        self.assertEqual(r.status, "F")

    def test_materiality_flags(self):
        self.assertTrue(self.line("Ops", "Travel").material)       # +120 and +20%

    def test_exact_threshold_not_material(self):
        self.assertFalse(self.line("Ops", "Rent").material)        # -30 but exactly -10.0%
        self.assertFalse(self.line("Eng", "Cloud").material)       # +600 but exactly +10.0%

    def test_run_rate_projection_on_line(self):
        r = self.line("Ops", "Travel")
        self.assertAlmostEqual(r.projected_fy, 720 + 120 * 6)      # flat 120/mo
        self.assertAlmostEqual(r.proj_var, 1440 - 1200)

    def test_rollups_sum_lines(self):
        ops = next(r for r in self.an.cost_centers if r.cost_center == "Ops")
        self.assertEqual(ops.ytd_actual, 720 + 270)
        self.assertEqual(ops.ytd_budget, 600 + 300)
        self.assertEqual(self.an.total.ytd_actual, sum(r.ytd_actual for r in self.an.lines))
        self.assertAlmostEqual(self.an.total.projected_fy, sum(r.projected_fy for r in self.an.lines))

    def test_top_drivers_by_absolute_dollars(self):
        names = [(r.cost_center, r.category) for r in top_drivers(self.an.lines, 3)]
        self.assertEqual(names, [("Eng", "Cloud"), ("Ops", "Travel"), ("Ops", "Rent")])

    def test_monthly_series(self):
        m = self.an.monthly
        self.assertEqual(len(m), 12)
        self.assertEqual(m[0]["actual"], 120 + 45 + 1100)
        self.assertIsNone(m[6]["actual"])
        self.assertAlmostEqual(m[6]["projection"], 120 + 45 + 1100)
        self.assertIsNone(m[2]["projection"])

    def test_explicit_through_ignores_later_actuals(self):
        an = analyze(self.b, self.a, through="2026-03")
        r = next(r for r in an.lines if r.category == "Travel")
        self.assertEqual((an.months_elapsed, r.ytd_actual, r.ytd_budget), (3, 360, 300))

    def test_missing_actuals_warn(self):
        a = dict(self.a)
        del a[("Ops", "Rent", "2026-04")]
        an = analyze(self.b, a, through="2026-06", allow_missing=True)
        self.assertTrue(any("Rent" in w for w in an.warnings))

    def test_line_only_in_actuals(self):
        a = dict(self.a)
        a[("Ops", "Legal", "2026-01")] = 500
        an = analyze(self.b, a, through="2026-06", thresholds=Thresholds(25, 0.10), allow_missing=True)
        legal = next(r for r in an.lines if r.category == "Legal")
        self.assertEqual((legal.ytd_budget, legal.ytd_actual, legal.var_pct), (0, 500, None))
        self.assertTrue(legal.material)


class TestCommentary(unittest.TestCase):
    def test_includes_top_driver_and_direction(self):
        b, a = build([("Tech", "Contractors", [10_000] * 12), ("Tech", "Software", [5_000] * 12),
                      ("Tech", "Salaries", [50_000] * 12)],
                     [("Tech", "Contractors", [15_000] * 6), ("Tech", "Software", [5_100] * 6),
                      ("Tech", "Salaries", [49_000] * 6)])
        an = analyze(b, a)
        cc = an.cost_centers[0]
        text = " ".join(cost_center_commentary(an, cc))
        self.assertIn("Largest driver: Contractors", text)
        self.assertIn("$30K (50.0%) over budget, unfavorable", text)
        self.assertIn("(material)", text)
        self.assertIn("Run-rate", text)

    def test_favorable_top_driver_and_offset(self):
        b, a = build([("Ops", "Staff", [100_000] * 12), ("Ops", "Travel", [10_000] * 12)],
                     [("Ops", "Staff", [90_000] * 6), ("Ops", "Travel", [15_000] * 6)])
        an = analyze(b, a)
        bullets = cost_center_commentary(an, an.cost_centers[0])
        self.assertTrue(any("Largest driver: Staff" in x and "under budget, favorable" in x for x in bullets))
        self.assertTrue(any(x.startswith("Also: Travel") for x in bullets))

    def test_no_material_message(self):
        b, a = build([("Ops", "Staff", [100] * 12)], [("Ops", "Staff", [101] * 6)])
        an = analyze(b, a)
        self.assertTrue(any("No line breaches" in x for x in cost_center_commentary(an, an.cost_centers[0])))

    def test_one_entry_per_cost_center(self):
        b, a = build([("A", "x", [1] * 12), ("B", "x", [1] * 12)], [("A", "x", [1] * 3), ("B", "x", [1] * 3)])
        self.assertEqual(set(all_commentary(analyze(b, a))), {"A", "B"})


class TestIOAndReport(unittest.TestCase):
    def tmp(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_load_sums_duplicates_and_skips_comments(self):
        p = self.tmp("# note\ncost_center,category,month,amount\nA,x,2026-01,\"1,000\"\nA,x,2026-01,$50\n")
        self.assertEqual(load_amounts(p), {("A", "x", "2026-01"): 1050.0})

    def test_load_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            load_amounts(self.tmp("cost_center,category,month,amount\nA,x,Jan-26,5\n"))
        with self.assertRaises(ValueError):
            load_amounts(self.tmp("cost_center,category,month,amount\nA,x,2026-01,abc\n"))
        with self.assertRaises(ValueError):
            load_amounts(self.tmp("cost_center,month\nA,2026-01\n"))

    def sample(self):
        root = os.path.join(os.path.dirname(__file__), "..", "data")
        return analyze(load_amounts(os.path.join(root, "budget.csv")), load_amounts(os.path.join(root, "actuals.csv")))

    def test_sample_data_shape(self):
        root = os.path.join(os.path.dirname(__file__), "..", "data")
        self.assertEqual(len(load_amounts(os.path.join(root, "budget.csv"))), 4 * 6 * 12)
        an = self.sample()
        self.assertEqual((len(an.lines), len(an.cost_centers), an.months_elapsed), (24, 4, 9))
        self.assertEqual(an.warnings, [])
        self.assertTrue(any(r.material for r in an.lines))

    def test_html_is_self_contained_and_escaped(self):
        html = build_html(self.sample())
        self.assertIn("<svg", html)
        self.assertIn("Salaries &amp; Benefits", html)
        for bad in ("<script", "<link", "src=", "href=", "url("):
            self.assertNotIn(bad, html)

    def test_svgs_parse(self):
        import xml.dom.minidom as md
        an = self.sample()
        md.parseString(waterfall_svg(an))
        md.parseString(monthly_svg(an))

    def test_waterfall_steps_reconcile(self):
        # budget + sum of all steps (drivers + other) must equal actual
        an = self.sample()
        drivers = top_drivers(an.lines, 6)
        other = an.total.var - sum(r.var for r in drivers)
        self.assertAlmostEqual(an.total.ytd_budget + sum(r.var for r in drivers) + other, an.total.ytd_actual)

    def test_csv_output(self):
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        out.close()
        self.addCleanup(os.unlink, out.name)
        write_csv(self.sample(), out.name)
        with open(out.name, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["level"] for r in rows].count("line"), 24)
        self.assertEqual({r["type"] for r in rows}, {"expense"})
        self.assertEqual(rows[-1]["level"], "total")
        self.assertIn(rows[-1]["fav_unfav"], {"Favorable", "Unfavorable", "On budget"})


ROOT = os.path.join(os.path.dirname(__file__), "..")
WIDE = os.path.join(ROOT, "data", "sample_wide.csv")


def tmp_csv(test, text):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write(text)
    f.close()
    test.addCleanup(os.unlink, f.name)
    return f.name


class TestRevenueSignConvention(unittest.TestCase):
    def analysis(self, budget_vals, actual_vals, kind):
        b, a = build([("NA", "Fees", budget_vals)], [("NA", "Fees", actual_vals)])
        return analyze(b, a, through="2026-03", kinds={("NA", "Fees"): kind})

    def test_revenue_above_budget_is_favorable(self):
        an = self.analysis([100] * 12, [120] * 12, REVENUE)
        r = an.lines[0]
        self.assertEqual((r.var, r.status, r.favorable), (60, "F", 60))

    def test_revenue_below_budget_is_unfavorable(self):
        r = self.analysis([100] * 12, [80] * 12, REVENUE).lines[0]
        self.assertEqual((r.var, r.status, r.favorable), (-60, "U", -60))

    def test_expense_logic_unchanged(self):
        r = self.analysis([100] * 12, [120] * 12, EXPENSE).lines[0]
        self.assertEqual((r.var, r.status, r.favorable), (60, "U", -60))

    def test_status_follows_kind(self):
        self.assertEqual(status(20, kind=REVENUE), "F")
        self.assertEqual(status(-20, kind=REVENUE), "U")
        self.assertEqual(status(20, kind=EXPENSE), "U")

    def test_type_column_default_is_expense(self):
        path = tmp_csv(self, "cost_center,category,month,amount\nA,x,2026-01,5\n")
        kinds = {}
        load_amounts(path, kinds)
        self.assertEqual(kinds, {})          # no type column: nothing recorded, so the analysis treats it as expense

    def test_type_column_is_case_insensitive(self):
        path = tmp_csv(self, "cost_center,category,month,amount,type\nA,x,2026-01,5,Revenue\n"
                             "A,y,2026-01,7, EXPENSE \n")
        kinds = {}
        load_amounts(path, kinds)
        self.assertEqual(kinds, {("A", "x"): REVENUE, ("A", "y"): EXPENSE})

    def test_unknown_type_raises_and_names_the_row(self):
        path = tmp_csv(self, "cost_center,category,month,amount,type\nA,x,2026-01,5,expense\n"
                             "A,y,2026-01,7,income\n")
        with self.assertRaises(ValueError) as cm:
            load_amounts(path)
        self.assertIn("row 3", str(cm.exception))
        self.assertIn("'income'", str(cm.exception))

    def test_line_cannot_switch_type(self):
        path = tmp_csv(self, "cost_center,category,month,amount,type\nA,x,2026-01,5,revenue\n"
                             "A,x,2026-02,7,expense\n")
        with self.assertRaises(ValueError):
            load_amounts(path, {})


class TestOperatingIncome(unittest.TestCase):
    def setUp(self):
        # Fees (revenue): budget 100, actual 90 per month. Payroll (expense): budget 60, actual 66.
        b, a = build([("NA", "Fees", [100] * 12), ("NA", "Payroll", [60] * 12)],
                     [("NA", "Fees", [90] * 12), ("NA", "Payroll", [66] * 12)])
        self.an = analyze(b, a, through="2026-01", kinds={("NA", "Fees"): REVENUE})

    def test_budget_and_actual_are_revenue_minus_expense(self):
        oi = self.an.operating_income
        self.assertEqual((oi.budget, oi.actual), (40, 24))
        self.assertEqual(oi.favorable, -16)

    def test_percent_uses_gross_budget(self):
        # gross budget = 100 + 60 = 160, so -16 is -10%, not -16 / 40
        self.assertAlmostEqual(self.an.operating_income.var_pct, -0.10)

    def test_summary_helper_matches(self):
        oi = summarize_operating_income(self.an.lines)
        self.assertEqual((oi.budget, oi.actual, oi.favorable, oi.gross_budget), (40, 24, -16, 160))

    def test_cost_center_and_total_rows_are_expense_only(self):
        self.assertEqual([r.category for r in self.an.lines if r.kind == EXPENSE], ["Payroll"])
        self.assertEqual(self.an.total.ytd_budget, 60)
        self.assertEqual(self.an.total.ytd_actual, 66)
        self.assertEqual([r.cost_center for r in self.an.cost_centers], ["NA"])

    def test_zero_budget_line_has_no_percent_but_still_gets_dollar_flag(self):
        b, a = build([("NA", "Fees", [0] * 12)], [("NA", "Fees", [30_000] * 12)])
        an = analyze(b, a, through="2026-01", kinds={("NA", "Fees"): REVENUE})
        r = an.lines[0]
        self.assertIsNone(r.var_pct)
        self.assertTrue(r.material)                      # $30,000 clears the $ test alone
        self.assertEqual(r.status, "F")
        self.assertIsNone(an.operating_income.var_pct)   # gross budget is zero

    def test_zero_budget_small_dollar_not_flagged(self):
        b, a = build([("NA", "Fees", [0] * 12)], [("NA", "Fees", [5_000] * 12)])
        an = analyze(b, a, through="2026-01", kinds={("NA", "Fees"): REVENUE})
        self.assertFalse(an.lines[0].material)


class TestBridge(unittest.TestCase):
    def analysis(self):
        budget, actuals, kinds = load_wide(WIDE)
        return analyze(budget, actuals, kinds=kinds)

    def test_steps_start_and_end_at_operating_income(self):
        steps = bridge_steps(self.analysis())
        self.assertEqual(steps[0][0], "Budget OI")
        self.assertEqual(steps[-1][0], "Actual OI")
        self.assertEqual([k for _, _, k in steps[1:-1]], ["delta"] * (len(steps) - 2))

    def test_bridge_reconciles_budget_to_actual(self):
        an = self.analysis()
        steps = bridge_steps(an)
        total = steps[0][1] + sum(v for _, v, k in steps if k == "delta")
        self.assertAlmostEqual(total, steps[-1][1])
        self.assertAlmostEqual(total, an.operating_income.actual)

    def test_bridge_reconciles_on_mixed_synthetic_data(self):
        b, a = build([("NA", "Fees", [100] * 12), ("NA", "Payroll", [60] * 12), ("EU", "Fees", [50] * 12)],
                     [("NA", "Fees", [120] * 6), ("NA", "Payroll", [70] * 6), ("EU", "Fees", [40] * 6)])
        an = analyze(b, a, through="2026-06", kinds={("NA", "Fees"): REVENUE, ("EU", "Fees"): REVENUE})
        steps = bridge_steps(an)
        self.assertAlmostEqual(steps[0][1] + sum(v for _, v, k in steps if k == "delta"), steps[-1][1])
        self.assertAlmostEqual(steps[-1][1] - steps[0][1], an.operating_income.favorable)

    def test_bridge_svg_truncated_axis_is_stated(self):
        import xml.dom.minidom as md
        svg = bridge_svg(self.analysis())
        md.parseString(svg)
        self.assertIn("Axis Starts at", svg)


class TestWideFormat(unittest.TestCase):
    def test_detects_wide_layout(self):
        self.assertTrue(is_wide(WIDE))
        self.assertFalse(is_wide(os.path.join(ROOT, "data", "budget.csv")))

    def test_wide_loads_budget_actual_and_types(self):
        budget, actuals, kinds = load_dataset(WIDE)
        self.assertEqual(len(budget), 12 * 3 * 5)
        self.assertEqual(len(actuals), 12 * 3 * 5)
        self.assertEqual(sum(1 for v in kinds.values() if v == REVENUE), 3 * 2)
        self.assertEqual(sum(1 for v in kinds.values() if v == EXPENSE), 3 * 3)
        self.assertEqual(budget[("EMEA", "Sales", "2026-01")], round(120_000 * 0.7))

    def test_wide_sample_analyzes_without_warnings(self):
        budget, actuals, kinds = load_dataset(WIDE)
        an = analyze(budget, actuals, kinds=kinds)
        self.assertEqual((an.months_elapsed, an.warnings), (12, []))
        self.assertEqual(len(an.lines), 15)
        self.assertEqual(len(an.cost_centers), 3)

    def test_operating_income_only_shown_when_data_has_revenue(self):
        import contextlib, io, shutil
        import cli
        from bva.charts import write_svgs
        # Expense-only data (the default sample): no operating-income block, section, or bridge chart
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--no-commentary"])
        self.assertNotIn("Operating income", buf.getvalue())
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        out = os.path.join(tmp, "narrow")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--no-commentary", "--charts", out, "--html", os.path.join(tmp, "n.html")])
        self.assertNotIn("bridge.svg", os.listdir(out))
        with open(os.path.join(tmp, "n.html")) as fh:
            html = fh.read()
        self.assertNotIn("Operating income", html)
        # Revenue data: block, section, and bridge chart all appear
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--budget", WIDE, "--no-commentary"])
        self.assertIn("Operating income (YTD, revenue less expense)", buf.getvalue())
        out = os.path.join(tmp, "wide")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--budget", WIDE, "--no-commentary", "--charts", out, "--html", os.path.join(tmp, "w.html")])
        self.assertIn("bridge.svg", os.listdir(out))
        with open(os.path.join(tmp, "w.html")) as fh:
            self.assertIn("Operating Income Bridge", fh.read())

    def test_has_revenue_flag(self):
        budget, actuals, kinds = load_dataset(WIDE)
        self.assertTrue(analyze(budget, actuals, kinds=kinds).has_revenue)
        b, a = build([("CC", "Travel", [100] * 12)], [("CC", "Travel", [110] * 12)])
        self.assertFalse(analyze(b, a).has_revenue)

    def test_narrow_needs_actuals_file(self):
        with self.assertRaises(ValueError):
            load_dataset(os.path.join(ROOT, "data", "budget.csv"))

    def test_wide_separate_actuals_file_rejected(self):
        with self.assertRaises(ValueError):
            load_dataset(WIDE, os.path.join(ROOT, "data", "actuals.csv"))

    def test_wide_unknown_type_raises_and_names_row(self):
        path = tmp_csv(self, "month,department,line_item,type,budget,actual\n"
                             "2026-01,NA,Sales,income,100,90\n")
        with self.assertRaises(ValueError) as cm:
            load_wide(path)
        self.assertIn("row 2", str(cm.exception))

    def test_wide_missing_column_raises(self):
        path = tmp_csv(self, "month,department,line_item,budget,actual\n2026-01,NA,Sales,100,90\n")
        with self.assertRaises(ValueError):
            load_wide(path)


class TestInputHardeningAndCliGate(unittest.TestCase):
    tmp = TestIOAndReport.tmp

    def test_accounting_negative_and_non_finite_amounts(self):
        p = self.tmp("cost_center,category,month,amount\nA,x,2026-01,\"($1,000.50)\"\n")
        self.assertEqual(load_amounts(p), {("A", "x", "2026-01"): -1000.5})
        for bad in ("nan", "inf", "-inf", "(abc)"):
            with self.assertRaises(ValueError, msg=bad):
                load_amounts(self.tmp(f"cost_center,category,month,amount\nA,x,2026-01,{bad}\n"))

    def run_cli(self, *args):
        import contextlib, io
        import cli
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            return cli.main(["--no-commentary", *args]), buf.getvalue()

    def test_fail_on_material_exit_code(self):
        self.assertEqual(self.run_cli()[0], 0)
        code, out = self.run_cli("--fail-on-material")
        self.assertEqual(code, 1)
        self.assertIn("--fail-on-material", out)
        self.assertEqual(self.run_cli("--fail-on-material", "--amount-threshold", "1e12")[0], 0)

    def test_bad_through_is_a_clean_cli_error(self):
        import contextlib, io
        import cli
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli.main(["--through", "2026-13"])
        self.assertEqual(cm.exception.code, 2)


class TestPvmProjectionJson(unittest.TestCase):
    tmp = TestIOAndReport.tmp
    PVM_ROWS = [(5000, 30, 4800, 35), (4000, 70, 4550, 75), (9000, 10, 6900, 10)]

    def run_cli(self, *args):
        import contextlib, io
        import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_price_volume_mix_example(self):
        from bva.pvm import price_volume_mix
        r = price_volume_mix(self.PVM_ROWS)
        self.assertAlmostEqual(r.budget_revenue, 520000)
        self.assertAlmostEqual(r.actual_revenue, 578250)
        self.assertAlmostEqual(r.price, 46750)
        self.assertAlmostEqual(r.volume + r.mix, 11500)
        self.assertAlmostEqual(r.price + r.volume + r.mix, r.total)
        self.assertAlmostEqual(r.volume, (16250 - 18000) * 520000 / 18000, places=6)

    def test_price_volume_mix_zero_budget_qty_raises(self):
        from bva.pvm import price_volume_mix
        with self.assertRaises(ValueError):
            price_volume_mix([(0, 10, 5, 10)])

    def test_load_pvm_rejects_missing_column_and_negative_qty(self):
        from bva.pvm import load_pvm
        p = self.tmp("product,budget_qty,budget_price,actual_qty\nA,1,2,3\n")
        with self.assertRaisesRegex(ValueError, "missing column"):
            load_pvm(p)
        p = self.tmp("product,budget_qty,budget_price,actual_qty,actual_price\nA,-1,30,5,35\n")
        with self.assertRaisesRegex(ValueError, "negative"):
            load_pvm(p)

    def test_budget_remaining_projection_and_method(self):
        from bva.variance import budget_remaining_projection
        self.assertAlmostEqual(budget_remaining_projection([10, 10], [5] * 12), 20 + 50)
        b, a = build([("A", "x", [100] * 12)], [("A", "x", [120, 90, 110])])
        an = analyze(b, a, method="budget-remaining")
        self.assertEqual(an.method, "budget-remaining")
        self.assertAlmostEqual(an.total.proj_var, an.total.var)

    def test_cli_json_is_valid_and_has_keys(self):
        import json
        code, out, _ = self.run_cli("--json", "--no-commentary")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue({"lines", "total", "projection"} <= set(payload))
        self.assertEqual(payload["projection"], "run-rate")

    def test_cli_json_with_fail_on_material_stays_valid_json(self):
        import json
        code, out, err = self.run_cli("--json", "--fail-on-material")
        self.assertEqual(code, 1)
        json.loads(out)  # stdout must still be pure JSON
        self.assertIn("material", err)

    def test_cli_pvm_example_returns_zero(self):
        code, out, _ = self.run_cli("--pvm", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                                           "data", "pvm_example.csv"))
        self.assertEqual(code, 0)
        self.assertIn("46,750.00", out)


class TestRound3(unittest.TestCase):
    def three_months(self):
        return build([("Ops", "Rent", [100] * 12)], [("Ops", "Rent", [100] * 3)])

    def test_closed_month_without_actuals_fails_closed(self):
        b, a = self.three_months()
        with self.assertRaisesRegex(ValueError, "no actuals"):
            analyze(b, a, through="2026-06")
        an = analyze(b, a, through="2026-06", allow_missing=True)
        self.assertTrue(any("no actuals" in w for w in an.warnings))
        self.assertEqual(an.total.ytd_actual, 300)

    def test_wrong_year_through_is_an_error(self):
        b, a = self.three_months()
        with self.assertRaisesRegex(ValueError, "FY2025"):
            analyze(b, a, through="2025-12")

    def test_gap_is_left_out_of_run_rate_average(self):
        self.assertAlmostEqual(run_rate_projection([100, None, 100], 9, n=3), 200 + 100 * 9)
        b, a = self.three_months()
        a = {k: v for k, v in a.items() if k[2] != "2026-03"}
        a[("Ops", "Rent", "2026-04")] = 100
        an = analyze(b, a, through="2026-04", run_rate_months=3, allow_missing=True)
        self.assertAlmostEqual(an.total.projected_fy, 300 + 100 * 8)
        self.assertAlmostEqual(an.monthly[5]["projection"], 100)

    def test_labels_follow_the_method(self):
        b, a = self.three_months()
        for method, label, col in (("run-rate", "Run-Rate", "projected_fy_run_rate"),
                                   ("budget-remaining", "Budget-Remaining", "projected_fy_budget_remaining")):
            an = analyze(b, a, method=method)
            self.assertIn(f"FY {label}", build_html(an))
            self.assertIn(f"{label} Projection", monthly_svg(an))
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "o.csv")
                write_csv(an, path)
                with open(path) as f:
                    self.assertIn(col, next(csv.reader(f)))
        self.assertNotIn("Run-Rate", build_html(analyze(b, a, method="budget-remaining")).split("<h2>Top")[0])

    def test_library_validates_arguments(self):
        b, a = self.three_months()
        for bad in ("2026-13", "2026-6", "bad", "2026-00"):
            with self.assertRaises(ValueError, msg=bad):
                analyze(b, a, through=bad)
        with self.assertRaises(ValueError):
            analyze(b, a, method="bogus")

    def test_all_zero_data_renders_every_chart(self):
        b, a = build([("Ops", "Rent", [0] * 12), ("Ops", "Fees", [0] * 12)],
                     [("Ops", "Rent", [0] * 2), ("Ops", "Fees", [0] * 2)])
        an = analyze(b, a)
        for fn in (waterfall_svg, monthly_svg, line_items_svg, heatmap_svg, cumulative_svg, flagged_svg):
            for dark in (False, True):
                svg = fn(an, dark=dark)
                ET.fromstring(svg)
                self.assertNotIn("nan", svg, fn.__name__)
                self.assertNotIn("inf", svg, fn.__name__)
        self.assertIn("<html", build_html(an))

    def test_short_csv_rows_are_value_errors(self):
        with tempfile.TemporaryDirectory() as d:
            narrow, wide = os.path.join(d, "n.csv"), os.path.join(d, "w.csv")
            with open(narrow, "w") as f:
                f.write("cost_center,category,month,amount\nA,x\n")
            with open(wide, "w") as f:
                f.write("month,department,line_item,type,budget,actual\n2026-01\n")
            for path in (narrow, wide):
                with self.assertRaises(ValueError, msg=path):
                    load_dataset(path, narrow)


if __name__ == "__main__":
    unittest.main()


ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestVisuals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expense, cls.wide = sample_analyses(ROOT)

    def test_every_svg_generated_light_and_dark_and_valid_xml(self):
        with tempfile.TemporaryDirectory() as d:
            names = build_docs(d)
            self.assertEqual(len(names), 18)
            for name in names:
                ET.parse(os.path.join(d, name))
            for name in names:
                if not name.endswith("-dark.svg"):
                    self.assertIn(name[:-4] + "-dark.svg", names)

    def test_dark_variant_is_transparent_light_is_white(self):
        with tempfile.TemporaryDirectory() as d:
            build_docs(d)
            with open(os.path.join(d, "heatmap.svg")) as f:
                light = f.read()
            with open(os.path.join(d, "heatmap-dark.svg")) as f:
                dark = f.read()
        self.assertIn(THEMES["light"]["bg"], light)
        self.assertNotIn('fill="#ffffff"', dark)
        self.assertIn(THEMES["dark"]["text"], dark)

    def test_cost_center_monthly_reconciles_to_rows(self):
        for r in self.expense.cost_centers:
            months = self.expense.cc_monthly[r.cost_center]
            self.assertEqual(len(months), 12)
            self.assertAlmostEqual(sum(m["budget"] for m in months), r.fy_budget)
            self.assertAlmostEqual(sum(m["actual"] for m in months if m["actual"] is not None), r.ytd_actual)
            self.assertTrue(all(m["actual"] is None for m in months[self.expense.months_elapsed:]))

    def test_line_items_and_cumulative_reconcile_to_total(self):
        exp = self.expense
        self.assertAlmostEqual(sum(r.ytd_budget for r in exp.expense_lines), exp.total.ytd_budget)
        self.assertAlmostEqual(sum(r.ytd_actual for r in exp.expense_lines), exp.total.ytd_actual)
        closed = exp.monthly[:exp.months_elapsed]
        self.assertAlmostEqual(sum(m["actual"] for m in closed), exp.total.ytd_actual)

    def test_heat_colors_are_diverging(self):
        pal = THEMES["light"]
        self.assertEqual(_heat(0.0, 0.1, pal), pal["panel"])
        self.assertEqual(_heat(0.1, 0.1, pal), pal["warm"])
        self.assertEqual(_heat(-0.1, 0.1, pal), pal["teal"])

    def test_banner_has_tagline_and_synthetic_label(self):
        svg = banner_svg()
        ET.fromstring(svg)
        self.assertIn("Budget vs Actual Variance, Materiality Flags and an Operating Income Bridge", svg)
        self.assertIn("Synthetic sample data", svg)

    def test_sample_output_is_real_cli_output(self):
        svg = sample_output_svg()
        ET.fromstring(svg)
        self.assertIn("$ python3 cli.py --no-commentary", svg)
        self.assertIn("29,646,400", svg)
        self.assertIn("Top 5 drivers", svg)

    def test_new_charts_parse_and_name_their_lines(self):
        for svg in (line_items_svg(self.expense), heatmap_svg(self.expense), cumulative_svg(self.expense),
                    flagged_svg(self.expense), bridge_svg(self.wide), monthly_svg(self.expense),
                    waterfall_svg(self.expense), line_items_svg(self.expense, dark=True)):
            ET.fromstring(svg)
        self.assertIn("Technology", flagged_svg(self.expense))
        self.assertIn("Budget", cumulative_svg(self.expense))

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "optional extra: matplotlib not installed")
    def test_matplotlib_pngs_written(self):
        import sys
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import plots_mpl
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(plots_mpl.main(d), 0)
            names = sorted(os.listdir(d))
            self.assertEqual(names, ["bridge.png", "cumulative.png", "flagged.png", "heatmap.png", "line-items.png"])
            for name in names:
                with open(os.path.join(d, name), "rb") as f:
                    self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

