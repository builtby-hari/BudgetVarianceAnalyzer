"""CSV loading. Two layouts are accepted.

Narrow (one file for budget, one for actuals): cost_center, category, month (YYYY-MM), amount,
  with an optional type column (expense or revenue; default expense when the column is absent).
Wide (one file with both sides): month, department, line_item, type, budget, actual.
  Chosen automatically when the header has budget and actual columns. The department becomes
  the cost center and the line item becomes the category.
Forecast (a third file): the narrow layout plus an optional as_of column (YYYY-MM), the last closed
  month when that forecast version was made. A forecast row must be for a month after its as_of.
"""

import csv
import math
import re

from .variance import EXPENSE, REVENUE

COLUMNS = ["cost_center", "category", "month", "amount"]
WIDE_COLUMNS = ["month", "department", "line_item", "type", "budget", "actual"]
TYPES = (EXPENSE, REVENUE)
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _reader(f):
    return csv.DictReader(line for line in f if not line.startswith("#"))


def _text(row, column, where):
    """A required text cell. A short row leaves it None, which is reported instead of crashing."""
    value = (row.get(column) or "").strip()
    if not value:
        raise ValueError(f"{where}: missing {column} (short or blank row)")
    return value


def _month(raw, where):
    month = (raw or "").strip()
    if not _MONTH.match(month):
        raise ValueError(f"{where}: month must be YYYY-MM, got {month!r}")
    return month


def _amount(raw, where):
    """Parse 1,234.50, $1,234.50 or accounting negatives like (1,234.50). nan and inf are rejected."""
    try:
        text = raw.strip().replace(",", "").replace("$", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        value = float(text)
    except (ValueError, AttributeError):
        raise ValueError(f"{where}: bad amount {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{where}: bad amount {raw!r}")
    return value


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


def load_amounts(path, kinds=None, versioned=False):
    """Return {(cost_center, category, 'YYYY-MM'): amount}. Duplicate keys are summed
    (like multiple GL postings). Lines starting with '#' are comments.
    If the file has a type column, each explicit type is recorded in `kinds`, keyed by
    (cost_center, category), so the analysis can flip the sign for revenue lines.
    With versioned=True the key gets a fourth part, the as_of month (None when the file has no
    as_of column), and each row must be for a month after its as_of."""
    kinds = {} if kinds is None else kinds
    out = {}
    with open(path, newline="") as f:
        reader = _reader(f)
        fields = reader.fieldnames or []
        missing = [c for c in COLUMNS if c not in fields]
        if missing:
            raise ValueError(f"{path}: missing column(s): {', '.join(missing)}")
        has_type, has_asof = "type" in fields, "as_of" in fields
        for n, row in enumerate(reader, start=2):
            where = f"{path} row {n}"
            month = _month(row["month"], where)
            amount = _amount(row["amount"], where)
            key = (_text(row, "cost_center", where), _text(row, "category", where), month)
            if has_type:
                _set_kind(kinds, key[:2], _type(row["type"], where), where)
            if versioned:
                as_of = _month(row["as_of"], where) if has_asof else None
                if as_of is not None and as_of >= month:
                    raise ValueError(f"{where}: as_of {as_of} must be before the forecast month {month}")
                key += (as_of,)
            out[key] = out.get(key, 0.0) + amount
    return out


def load_forecast(path, kinds=None):
    """Return {(cost_center, category, month, as_of): amount} from a forecast file (as_of may be None)."""
    return load_amounts(path, kinds, versioned=True)


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
            key = (_text(row, "department", where), _text(row, "line_item", where), _month(row["month"], where))
            _set_kind(kinds, key[:2], _type(row["type"], where), where)
            budget[key] = budget.get(key, 0.0) + _amount(row["budget"], where)
            if (row.get("actual") or "").strip():   # blank actual = month not closed yet; budget still counts
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
