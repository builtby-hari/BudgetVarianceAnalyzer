"""Revenue price / volume / mix decomposition against a budget, plus the flexed budget.

Per product i, with budget quantity BQ and price BP, actual quantity AQ and price AP:
  price  = sum((AP - BP) * AQ)
  volume = (sum(AQ) - sum(BQ)) * average budget price   (average = sum(BQ*BP) / sum(BQ))
  mix    = sum((AQ - sum(AQ) * BQ / sum(BQ)) * BP)      (actual units less actual total at budget mix)
price + volume + mix = actual revenue - budget revenue exactly. Flexed revenue = sum(AQ * BP), so
volume + mix = flexed - static budget (sales volume variance) and price = actual - flexed (flex variance).
Positive is favorable (more revenue). Sources are listed in the README.
"""

import csv
from dataclasses import dataclass
from typing import List

from .data import _amount, _reader

PVM_COLUMNS = ["product", "budget_qty", "budget_price", "actual_qty", "actual_price"]


@dataclass(frozen=True)
class PVM:
    budget_revenue: float
    flexed_revenue: float
    actual_revenue: float
    price: float
    volume: float
    mix: float

    @property
    def total(self):
        return self.actual_revenue - self.budget_revenue


def price_volume_mix(rows) -> PVM:
    """rows: iterable of (budget_qty, budget_price, actual_qty, actual_price) per product."""
    rows = list(rows)
    bq = sum(r[0] for r in rows)
    if bq <= 0:
        raise ValueError("total budget quantity must be positive to split volume from mix")
    aq = sum(r[2] for r in rows)
    budget = sum(r[0] * r[1] for r in rows)
    flexed = sum(r[2] * r[1] for r in rows)
    actual = sum(r[2] * r[3] for r in rows)
    price = actual - flexed
    volume = (aq - bq) * budget / bq
    return PVM(budget, flexed, actual, price, volume, (flexed - budget) - volume)


def load_pvm(path) -> List[tuple]:
    """Read product, budget_qty, budget_price, actual_qty, actual_price. Returns (name, bq, bp, aq, ap) tuples."""
    out = []
    with open(path, newline="") as f:
        reader = _reader(f)
        missing = [c for c in PVM_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s): {', '.join(missing)}")
        for n, row in enumerate(reader, start=2):
            where = f"{path} row {n}"
            vals = [_amount(row[c], where) for c in PVM_COLUMNS[1:]]
            if vals[0] < 0 or vals[2] < 0:
                raise ValueError(f"{where}: quantities cannot be negative")
            out.append((row["product"].strip(), *vals))
    if not out:
        raise ValueError(f"{path}: no rows")
    return out
