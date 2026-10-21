#!/usr/bin/env python3
"""Optional matplotlib versions of the README charts, written as PNG to docs/mpl/.

The core project is standard-library only. This script needs matplotlib
(pip install -r requirements-extra.txt); without it the script exits with a message.
All data is synthetic.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAVY, TEAL, WARM, MUTED, GRID, TEXT = "#1f3a5f", "#14b8a6", "#c2410c", "#94a3b8", "#cbd5e1", "#334155"
SYNTHETIC = "Synthetic sample data"


def _style(plt):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": TEXT,
                         "axes.labelcolor": TEXT, "xtick.color": TEXT, "ytick.color": TEXT,
                         "axes.spines.top": False, "axes.spines.right": False})


def _save(fig, outdir, name):
    path = os.path.join(outdir, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    return path


def main(outdir=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LinearSegmentedColormap
    except ImportError:
        print("matplotlib is not installed. It is an optional extra: pip install -r requirements-extra.txt")
        print("The core tool and its charts do not need it.")
        return 0

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from bva.charts import sample_analyses
    from bva.variance import bridge_steps, top_drivers

    outdir = outdir or os.path.join(HERE, "docs", "mpl")
    os.makedirs(outdir, exist_ok=True)
    _style(plt)
    expense, wide = sample_analyses(HERE)
    written = []

    # 1. operating income bridge (wide sample, revenue and expense)
    steps = bridge_steps(wide)
    fig, ax = plt.subplots(figsize=(10, 5.4))
    running = 0.0
    for i, (label, value, kind) in enumerate(steps):
        if kind == "total":
            bottom, height, color = 0.0, value, (MUTED if i == 0 else NAVY)
            running = value
            ax.bar(i, height, bottom=bottom, color=color, width=0.62)
            ax.text(i, value, f"{value/1e6:.2f}M", ha="center", va="bottom", fontsize=9)
        else:
            start = running
            running += value
            bottom, height = min(start, running), abs(value)
            ax.bar(i, height, bottom=bottom, color=(TEAL if value >= 0 else WARM), width=0.62)
            ax.text(i, max(start, running), f"{value/1e3:+,.0f}K", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([s[0] for s in steps], rotation=35, ha="right")
    lo = min(min(v for _, v, k in steps if k == "total"), running) * 0.97
    ax.set_ylim(lo, max(v for _, v, k in steps if k == "total") * 1.02)
    ax.set_ylabel("Operating Income, YTD (Axis Truncated)")
    ax.set_title(f"Operating Income Bridge: Budget to Actual ({wide.through})\n{SYNTHETIC}", color=TEXT, loc="left")
    written.append(_save(fig, outdir, "bridge.png"))
    plt.close(fig)

    # 2. budget vs actual by line item (expense, all cost centers)
    groups = {}
    for r in expense.expense_lines:
        g = groups.setdefault(r.category, [0.0, 0.0])
        g[0] += r.ytd_budget
        g[1] += r.ytd_actual
    items = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0]))
    fig, ax = plt.subplots(figsize=(10, 5.4))
    xs = range(len(items))
    ax.bar([x - 0.2 for x in xs], [v[0] / 1e6 for _, v in items], width=0.38, color=MUTED, label="Budget")
    ax.bar([x + 0.2 for x in xs], [v[1] / 1e6 for _, v in items], width=0.38, color=NAVY, label="Actual")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([name for name, _ in items], rotation=0, wrap=True)
    ax.set_ylabel("YTD Spend ($M)")
    ax.legend(frameon=False)
    ax.set_title(f"YTD Budget vs Actual by Line Item ({expense.through})\n{SYNTHETIC}", color=TEXT, loc="left")
    written.append(_save(fig, outdir, "line-items.png"))
    plt.close(fig)

    # 3. variance % heatmap, cost center x month, plus expense total
    n = expense.months_elapsed
    rows = [(r.cost_center, [(m["actual"] - m["budget"]) / m["budget"] * 100 for m in expense.cc_monthly[r.cost_center][:n]])
            for r in expense.cost_centers]
    rows.append(("Total", [(m["actual"] - m["budget"]) / m["budget"] * 100 for m in expense.monthly[:n]]))
    data = [cells for _, cells in rows]
    lim = max(abs(v) for cells in data for v in cells)
    cmap = LinearSegmentedColormap.from_list("var", [TEAL, "#f8fafc", WARM])
    fig, ax = plt.subplots(figsize=(10, 4.6))
    im = ax.imshow(data, cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
    for i, cells in enumerate(data):
        for j, v in enumerate(cells):
            ax.text(j, i, f"{v:+.1f}%", ha="center", va="center", fontsize=9, color=TEXT)
    ax.set_xticks(range(n))
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][:n])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([name for name, _ in rows])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Variance % vs Budget")
    ax.set_title(f"Variance % vs Budget by Cost Center and Month (Expense)\n{SYNTHETIC}", color=TEXT, loc="left")
    written.append(_save(fig, outdir, "heatmap.png"))
    plt.close(fig)

    # 4. cumulative YTD budget vs actual
    cb, ca, cum_b, cum_a = 0.0, 0.0, [], []
    for m in expense.monthly[:n]:
        cb += m["budget"]
        ca += m["actual"]
        cum_b.append(cb / 1e6)
        cum_a.append(ca / 1e6)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][:n]
    fig, ax = plt.subplots(figsize=(10, 5.0))
    ax.plot(months, cum_b, color=MUTED, lw=2.4, marker="o", label="Budget")
    ax.plot(months, cum_a, color=NAVY, lw=2.4, marker="o", label="Actual")
    ax.set_ylabel("Cumulative Spend ($M, Axis Truncated)")
    ax.legend(frameon=False, loc="upper left")
    ax.set_title(f"Cumulative YTD Budget vs Actual, Expense Total ({expense.through})\n{SYNTHETIC}", color=TEXT, loc="left")
    gap = cum_a[-1] - cum_b[-1]
    ax.annotate(f"{gap*1e3:+,.0f}K vs Budget", xy=(n - 1, cum_a[-1]), xytext=(-120, 18),
                textcoords="offset points", color=WARM if gap > 0 else TEAL, fontsize=9)
    written.append(_save(fig, outdir, "cumulative.png"))
    plt.close(fig)

    # 5. flagged line items: largest variances, favorable and unfavorable
    rows = top_drivers(expense.lines, 10)
    fig, ax = plt.subplots(figsize=(10, 5.4))
    labels = [f"{r.cost_center} / {r.category}" + (" *" if r.material else "") for r in rows]
    for i, r in enumerate(rows):
        color = WARM if r.status == "U" else (TEAL if r.status == "F" else MUTED)
        ax.barh(i, r.var / 1e3, color=color, alpha=1.0 if r.material else 0.4)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.axvline(0, color=TEXT, lw=0.8)
    ax.set_xlabel("YTD Variance, $K (Actual - Budget; Positive = Over Budget)")
    ax.set_title(f"Flagged Line Items: Largest Variances ({expense.through})\n{SYNTHETIC}. * = material.",
                 color=TEXT, loc="left")
    written.append(_save(fig, outdir, "flagged.png"))
    plt.close(fig)

    for path in written:
        print("wrote", path if not path.startswith(HERE) else os.path.relpath(path, HERE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
