#!/usr/bin/env python3
"""Generate data/budget.csv and data/actuals.csv: SYNTHETIC FP&A data (not any real company).

4 cost centers x 6 expense categories x 12 months of budget; actuals for Jan-Sep 2026.
Seeded for reproducibility. Run from the project root:  python3 data/generate_sample.py
"""

import csv
import os
import random

YEAR, CLOSED = 2026, 9
HERE = os.path.dirname(os.path.abspath(__file__))
CATS = ["Salaries & Benefits", "Contractors", "Software & Data", "Travel & Training",
        "Professional Services", "Facilities & Other"]
# annual budget in $K per cost center, in CATS order
ANNUAL_K = {
    "Credit Reporting": [5200, 900, 700, 150, 600, 650],
    "Capital Markets":  [7400, 800, 1500, 600, 450, 750],
    "FP&A":             [3300, 300, 350, 120, 500, 430],
    "Technology":       [8000, 2800, 2600, 160, 300, 500],
}
# monthly phasing weights (sum to 12): flat, with seasonality for travel and professional fees
SEASON = {
    "Travel & Training": [0.5, 0.7, 1.1, 1.2, 1.3, 1.0, 0.6, 0.7, 1.4, 1.5, 1.2, 0.8],
    "Professional Services": [0.8, 1.0, 1.0, 0.8, 0.8, 0.9, 0.8, 0.8, 1.1, 1.3, 1.4, 1.3],
}
# (cost center, category) -> (start month, actual/budget drift): the planted storylines
DRIFT = {
    ("Technology", "Contractors"): (3, 0.28),            # backfill ramp-up
    ("Technology", "Software & Data"): (1, 0.12),         # license true-up
    ("Capital Markets", "Travel & Training"): (2, 0.30),  # more conferences and desk travel
    ("Capital Markets", "Software & Data"): (5, 0.07),
    ("Credit Reporting", "Salaries & Benefits"): (1, -0.05),   # open headcount
    ("FP&A", "Professional Services"): (1, -0.25),        # deferred consulting
    ("FP&A", "Software & Data"): (4, 0.15),
}


def main():
    rng = random.Random(2026)
    budget, actual = [], []
    for cc, annual in ANNUAL_K.items():
        for cat, k in zip(CATS, annual):
            w = SEASON.get(cat, [1.0] * 12)
            assert abs(sum(w) - 12) < 1e-9, cat
            for m in range(1, 13):
                b = round(k * 1000 / 12 * w[m - 1], -2)
                month = f"{YEAR}-{m:02d}"
                budget.append([cc, cat, month, f"{b:.0f}"])
                if m <= CLOSED:
                    start, d = DRIFT.get((cc, cat), (1, 0.0))
                    drift = d if m >= start else 0.0
                    a = round(b * (1 + drift + rng.uniform(-0.03, 0.03)), -2)
                    actual.append([cc, cat, month, f"{a:.0f}"])
    for name, rows, note in (("budget.csv", budget, "FY2026 budget"), ("actuals.csv", actual, "FY2026 actuals, Jan-Sep")):
        with open(os.path.join(HERE, name), "w", newline="") as f:
            f.write(f"# SYNTHETIC SAMPLE DATA ({note}): invented cost centers and amounts, not real financials.\n")
            w = csv.writer(f)
            w.writerow(["cost_center", "category", "month", "amount"])
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {name}")


if __name__ == "__main__":
    main()
