"""Excel export with live formulas, written with the standard library only (an .xlsx is zipped XML).

One sheet. Blue cells are inputs: the materiality thresholds and each line's YTD budget, YTD
actual, FY budget and FY projection. Black cells are formulas: variance $ and %, favorable or
unfavorable, materiality and projected variance, plus SUMIFS subtotals by cost center and an
expense total. Cached values are written too, so viewers that do not recalculate still show numbers.
"""
import zipfile
from xml.sax.saxutils import escape

from .variance import EXPENSE

TOL = 0.005  # same on-budget band as variance.status
HEAD = ["Cost Center", "Category", "Type", "YTD Budget", "YTD Actual", "Var $", "Var %", "F/U", "Material",
        "FY Budget", "FY Projection", "Proj. Var $", "Proj. Var %"]
FIRST = 6  # first data row; thresholds sit in B2:B3, header in row 5

# Style ids in STYLES: 0 text, 1 input $, 2 formula $, 3 formula %, 4 header, 5 input %, 6 bold formula $, 7 bold text
STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>
<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><sz val="11"/><color rgb="FF0000FF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="8"><xf/><xf numFmtId="3" fontId="1" applyNumberFormat="1" applyFont="1"/><xf numFmtId="3" applyNumberFormat="1"/>
<xf numFmtId="164" applyNumberFormat="1"/><xf fontId="2" fillId="2" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="1" applyNumberFormat="1" applyFont="1"/><xf numFmtId="3" fontId="3" applyNumberFormat="1" applyFont="1"/>
<xf fontId="3" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

PARTS = {
    "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
    "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
    "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Variance" sheetId="1" r:id="rId1"/></sheets><calcPr fullCalcOnLoad="1"/></workbook>""",
    "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
    "xl/styles.xml": STYLES,
}


def _cell(ref, v, style=0, formula=None):
    """Number, text, or formula with a cached result (None for an empty result)."""
    f = f"<f>{escape(formula)}</f>" if formula else ""
    if isinstance(v, str):
        if formula:
            return f'<c r="{ref}" s="{style}" t="str">{f}<v>{escape(v)}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
    return f'<c r="{ref}" s="{style}">{f}' + ("" if v is None else f"<v>{v!r}</v>") + "</c>"


def _row(n, cells):
    return f'<row r="{n}">' + "".join(cells) + "</row>"


def _calc(n, r):
    """Formula cells F..I and L..M for row n, with the analysis values cached."""
    flip = f'IF(C{n}="{EXPENSE}",1,-1)'  # expense: over budget is unfavorable; revenue: the reverse
    return [
        _cell(f"F{n}", r.var, 2, f"E{n}-D{n}"),
        _cell(f"G{n}", "" if r.var_pct is None else r.var_pct, 3, f'IF(D{n}=0,"",F{n}/ABS(D{n}))'),
        _cell(f"H{n}", {"U": "U", "F": "F", "-": "-"}[r.status], 0,
              f'IF(F{n}*{flip}>{TOL},"U",IF(F{n}*{flip}<-{TOL},"F","-"))'),
        _cell(f"I{n}", "Y" if r.material else "N", 0,
              f'IF(AND(ABS(F{n})>$B$2,OR(D{n}=0,ABS(F{n})>$B$3*ABS(D{n}))),"Y","N")'),
        _cell(f"L{n}", r.proj_var, 2, f"K{n}-J{n}"),
        _cell(f"M{n}", "" if r.proj_var_pct is None else r.proj_var_pct, 3, f'IF(J{n}=0,"",L{n}/ABS(J{n}))'),
    ]


def write_xlsx(analysis, th, path):
    lines = sorted(analysis.lines, key=lambda r: (r.cost_center, r.category))
    last = FIRST + len(lines) - 1
    rows = [
        _row(1, [_cell("A1", f"FY{analysis.year} Budget vs Actual, through {analysis.through}", 7)]),
        _row(2, [_cell("A2", "Materiality: $ threshold"), _cell("B2", th.amount, 1)]),
        _row(3, [_cell("A3", "Materiality: % threshold (both must breach)"), _cell("B3", th.pct, 5)]),
        _row(5, [_cell(f"{chr(65 + i)}5", h, 4) for i, h in enumerate(HEAD)]),
    ]
    for n, r in enumerate(lines, FIRST):
        rows.append(_row(n, [_cell(f"A{n}", r.cost_center), _cell(f"B{n}", r.category or ""), _cell(f"C{n}", r.kind),
                             _cell(f"D{n}", r.ytd_budget, 1), _cell(f"E{n}", r.ytd_actual, 1)] + _calc(n, r)[:4]
                            + [_cell(f"J{n}", r.fy_budget, 1), _cell(f"K{n}", r.projected_fy, 1)] + _calc(n, r)[4:]))
    n = last + 2
    rows.append(_row(n, [_cell(f"A{n}", "Expense Subtotals by Cost Center", 7)]))
    for r in analysis.cost_centers + [analysis.total]:
        n += 1
        crit = f'$A${FIRST}:$A${last},A{n},' if r is not analysis.total else ""
        sums = {c: f'SUMIFS({c}${FIRST}:{c}${last},{crit}$C${FIRST}:$C${last},"{EXPENSE}")' for c in "DEJK"}
        bold = 6 if r is analysis.total else 2
        rows.append(_row(n, [_cell(f"A{n}", r.cost_center, 7 if r is analysis.total else 0), _cell(f"C{n}", EXPENSE),
                             _cell(f"D{n}", r.ytd_budget, bold, sums["D"]), _cell(f"E{n}", r.ytd_actual, bold, sums["E"])]
                            + _calc(n, r)[:4]
                            + [_cell(f"J{n}", r.fy_budget, bold, sums["J"]), _cell(f"K{n}", r.projected_fy, bold, sums["K"])]
                            + _calc(n, r)[4:]))
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                   for i, w in enumerate([24, 22, 10] + [14] * 10, 1))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="5" topLeftCell="A6" state="frozen"/></sheetView></sheetViews>'
             f"<cols>{cols}</cols><sheetData>" + "".join(rows) + "</sheetData></worksheet>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in PARTS.items():
            z.writestr(name, text)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
