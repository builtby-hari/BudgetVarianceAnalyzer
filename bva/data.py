"""CSV loading. Two layouts are accepted.

Narrow (one file for budget, one for actuals): cost_center, category, month (YYYY-MM), amount,
  with an optional type column (expense or revenue; default expense when the column is absent).
Wide (one file with both sides): month, department, line_item, type, budget, actual.
  Chosen automatically when the header has budget and actual columns. The department becomes
  the cost center and the line item becomes the category.
"""

import csv
import re

from .variance import EXPENSE, REVENUE

COLUMNS = ["cost_center", "category", "month", "amount"]
WIDE_COLUMNS = ["month", "department", "line_item", "type", "budget", "actual"]
TYPES = (EXPENSE, REVENUE)
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _reader(f):
    return csv.DictReader(line for line in f if not line.startswith("#"))


def _month(raw, where):
    month = (raw or "").strip()
    if not _MONTH.match(month):
        raise ValueError(f"{where}: month must be YYYY-MM, got {month!r}")
    return month


def _amount(raw, where):
    try:
        return float(raw.replace(",", "").replace("$", ""))
    except (ValueError, AttributeError):
        raise ValueError(f"{where}: bad amount {raw!r}") from None


def _type(raw, where):
    kind = (raw or "").strip().lower()
    if kind not in TYPES:
        raise ValueError(f"{where}: type must be expense or revenue, got {raw!r}")
    return kind


def _set_kind(kinds, key, kind, where):
    """Record a line's type. A line cannot be revenue in one row and expense in another."""
    prev = kinds.setdefault(key, kind)
    if prev != kind:
        raise ValueError(f"{where}: {key[0]} / {key[1]} is typed {kind} here but {prev} elsewhere")


def load_amounts(path, kinds=None):
    """Return {(cost_center, category, 'YYYY-MM'): amount}. Duplicate keys are summed
    (like multiple GL postings). Lines starting with '#' are comments.
    If the file has a type column, each explicit type is recorded in `kinds`, keyed by
    (cost_center, category), so the analysis can flip the sign for revenue lines."""
    kinds = {} if kinds is None else kinds
    out = {}
    with open(path, newline="") as f:
        reader = _reader(f)
        fields = reader.fieldnames or []
        missing = [c for c in COLUMNS if c not in fields]
        if missing:
            raise ValueError(f"{path}: missing column(s): {', '.join(missing)}")
        has_type = "type" in fields
        for n, row in enumerate(reader, start=2):
            where = f"{path} row {n}"
            month = _month(row["month"], where)
            amount = _amount(row["amount"], where)
            key = (row["cost_center"].strip(), row["category"].strip(), month)
            if has_type:
                _set_kind(kinds, key[:2], _type(row["type"], where), where)
            out[key] = out.get(key, 0.0) + amount
    return out


def is_wide(path):
    """True when the header has both budget and actual columns (the wide layout)."""
    with open(path, newline="") as f:
        fields = _reader(f).fieldnames or []
    return "budget" in fields and "actual" in fields


def load_wide(path):
    """Return (budget, actuals, kinds) from the wide layout, in the same shapes as load_amounts."""
    budget, actuals, kinds = {}, {}, {}
    with open(path, newline="") as f:
        reader = _reader(f)
        missing = [c for c in WIDE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s): {', '.join(missing)}")
        for n, row in enumerate(reader, start=2):
            where = f"{path} row {n}"
            key = (row["department"].strip(), row["line_item"].strip(), _month(row["month"], where))
            _set_kind(kinds, key[:2], _type(row["type"], where), where)
            budget[key] = budget.get(key, 0.0) + _amount(row["budget"], where)
            actuals[key] = actuals.get(key, 0.0) + _amount(row["actual"], where)
    return budget, actuals, kinds


def load_dataset(budget_path, actuals_path=None):
    """Return (budget, actuals, kinds) from either layout. A wide file holds both sides,
    so pass it as budget_path alone. The narrow layout needs a separate actuals_path."""
    if is_wide(budget_path):
        if actuals_path not in (None, budget_path):
            raise ValueError(f"{budget_path} is wide (budget and actual in one file); "
                             "do not pass a separate actuals file")
        return load_wide(budget_path)
    if actuals_path is None:
        raise ValueError(f"{budget_path} is narrow; an actuals file is required")
    kinds = {}
    return load_amounts(budget_path, kinds), load_amounts(actuals_path, kinds), kinds
