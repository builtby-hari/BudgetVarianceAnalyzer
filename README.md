# Budget Variance Analyzer

[![tests](https://github.com/builtby-hari/BudgetVarianceAnalyzer/actions/workflows/tests.yml/badge.svg)](https://github.com/builtby-hari/BudgetVarianceAnalyzer/actions/workflows/tests.yml) ![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

The monthly budget-vs-actual close, as one command. Give it a budget file and an actuals file. It shows where spend is off, which gaps are big enough to need an explanation, and where the year lands at the current pace.

On the sample data (synthetic FY2026, through September), total spend is **$547.7K (1.9%) over budget**, and one line, Technology contractors, is $454.5K over on its own.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/heatmap-dark.svg">
  <img src="docs/heatmap.svg" alt="Heatmap of spend variance vs budget by cost center and month, January to September. Technology runs roughly 7 to 9 percent over budget from March on, while Finance and Operations stay under." width="820">
</picture>

## What it does

```mermaid
flowchart LR
    B["budget.csv"] --> L["Loader<br/>rejects bad rows"]
    A["actuals.csv"] --> L
    L --> V["Variance engine<br/>actual minus budget,<br/>materiality flags"]
    V --> P["Full-year projection<br/>and rolling forecast"]
    V --> C["Commentary draft"]
    V --> O["Operating income bridge"]
    P --> R["Terminal, CSV, HTML,<br/>Excel, SVG charts"]
    C --> R
    O --> R
```

Every output reads from the same variance engine, so the Excel pack, the HTML report and the charts always tie to the same numbers.

A few terms, in plain words:

- **Variance:** actual minus budget. For costs, over budget is unfavorable (U) and under is favorable (F). For revenue it flips.
- **Materiality:** the rule for which gaps need a written explanation. Here a line has to be off by more than $25K and more than 10%. Both, not either.
- **Run-rate:** the full-year estimate if the rest of the year looks like the last three months.
- **Bridge:** a waterfall chart that walks from the budget number to the actual number one driver at a time.
- **Price, volume, mix (PVM):** splits a revenue gap into three causes: charging different prices, selling a different number of units, and selling a different blend of products.

## The result (synthetic FY2026 data, through September)

- Total spend is **$547.7K (1.9%) over** the year-to-date budget. Technology accounts for $687K of it.
- **Technology / Contractors** is $454.5K (21.6%) over. That clears both materiality tests, so it's flagged. At the last three months' pace it finishes about **$650K over** the full-year budget.
- A big line can stay unflagged. Operations salaries are $201K under budget, but that's only 5.2%, so they don't need an explanation.
- Price, volume and mix example: revenue beat budget by $58.3K. Higher prices added $46.8K and a richer product mix added $62.1K, which more than covered $50.6K lost on 1,750 fewer units.

The ten biggest gaps, with the material ones starred:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/flagged-dark.svg">
  <img src="docs/flagged.svg" alt="Horizontal bars of the ten largest line-item variances. Technology contractors lead at $454K over budget (21.6%), then Technology software and data at $240K over. Operations salaries are $201K under." width="820">
</picture>

Where the full year lands if the forecast holds, walked from the budget one driver at a time:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/forecast-outlook-dark.svg">
  <img src="docs/forecast-outlook.svg" alt="Waterfall from the $39.1M full-year budget to a $39.9M outlook. Technology contractors add $647K and Technology software adds $313K, partly offset by Operations salaries at $238K under." width="820">
</picture>

The same idea for operating income, on a second sample file that has revenue lines (full year):

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/bridge-dark.svg">
  <img src="docs/bridge.svg" alt="Operating income bridge from a $2.0M budget to a $1.8M actual. Sales ($66K), services ($57K) and payroll ($54K) are the three biggest drags." width="820">
</picture>

And what the terminal prints:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/sample-output-dark.svg">
  <img src="docs/sample-output.svg" alt="Terminal output of the tool: a table of year-to-date budget, actual and variance by cost center, followed by the top five drivers" width="820">
</picture>

## Real companies (SEC filings)

The same engine also runs on public companies. They do not publish budgets, so the comparison is against something they do publish.

```mermaid
flowchart LR
    S["SEC filing data"] --> Y["yoy<br/>baseline: same period last year"]
    S --> Q["qoq<br/>baseline: previous quarter"]
    S --> G["guidance<br/>baseline: guidance midpoint<br/>that you type in"]
    S --> M["segments<br/>baseline: last year's segment figures"]
    Y --> E["Variance engine<br/>actual minus baseline"]
    Q --> E
    G --> E
    M --> E
```

Try it on the bundled Home Depot data, no network needed:

```bash
python3 cli.py sec --ticker HD --facts tests/fixtures/hd_companyfacts.json --mode yoy
```

Home Depot's fiscal 2025 sales rose $5.2B, but operating income fell $636M. Higher cost of sales ($3.6B) and SG&A ($2.0B) took more than the extra sales brought in.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/sec-hd-bridge-dark.svg">
  <img src="docs/sec-hd-bridge.svg" alt="Home Depot operating income bridge from $21.5B in the prior year to $20.9B. Revenue adds $5.17B. Cost of revenue takes $3.61B, SG&A takes $1.95B and depreciation takes $239M." width="820">
</picture>

Segments read straight from a 10-K. Microsoft's Intelligent Cloud grew 29.7% and More Personal Computing slipped 1.1%:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/sec-msft-segments-dark.svg">
  <img src="docs/sec-msft-segments.svg" alt="Microsoft segment revenue, prior year against current year. Productivity and Business Processes $140.0B, Intelligent Cloud $137.8B, More Personal Computing $54.1B." width="820">
</picture>

Every number lists its filing, period, accession number and XBRL tag. Details, flags and limits: [docs/public-companies.md](docs/public-companies.md).

## Run it

Try it in one line. No installs, it runs on the sample data:

```bash
git clone https://github.com/builtby-hari/BudgetVarianceAnalyzer.git && cd BudgetVarianceAnalyzer && python3 cli.py --through 2026-09
```

Python 3.9+. No installs needed for the core tool.

```bash
python3 cli.py --through 2026-09                     # the summary above
python3 cli.py --through 2026-09 --xlsx output/pack.xlsx   # Excel with live formulas
python3 cli.py --forecast data/forecast.csv          # rolling forecast, accuracy and commentary
python3 cli.py --pvm data/pvm_example.csv            # price / volume / mix
python3 -m unittest discover                         # tests
```

`--fail-on-material` exits with status 1 if any line is material, so it can gate a close checklist.

`python3 -m bva.charts` redraws every chart on this page from the sample data. It needs no installs.

## What I fixed along the way

- Closed months with no actuals used to count as zero spend, which made lines look under budget. The tool now stops and names the missing month.
- CSVs saved from Excel carry a hidden byte-order mark that broke the header match. They now load cleanly.

The sample data in `data/` is synthetic. The public-company fixtures in `tests/fixtures` are trimmed copies of real SEC filings (Home Depot and Microsoft). Full concepts, formulas and methods: [docs/reference.md](docs/reference.md).
