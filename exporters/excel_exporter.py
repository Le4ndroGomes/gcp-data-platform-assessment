"""assessment_report.xlsx — uma aba por CSV entregável (requer openpyxl; opcional)."""
from __future__ import annotations

from pathlib import Path

from exporters.csv_exporter import _cell


def write_excel(path: Path, tables: dict[str, tuple[list[dict], list[str] | None]], summary_rows: list[tuple[str, str]]) -> Path | None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    head = PatternFill("solid", fgColor="1B3139")
    ws.append(["Indicador", "Valor"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head
    for k, v in summary_rows:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 60
    for name, (rows, cols) in tables.items():
        sheet = wb.create_sheet(name.replace(".csv", "")[:31])
        if cols is None:
            cols = []
            for r in rows or []:
                for k in r:
                    if k not in cols and not k.startswith("_"):
                        cols.append(k)
        cols = cols or ["(sem dados)"]
        sheet.append(cols)
        for c in sheet[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = head
            c.alignment = Alignment(vertical="center")
        for r in (rows or [])[:200_000]:
            vals = []
            for k in cols:
                v = _cell(r.get(k))
                if isinstance(v, str) and len(v) > 32_000:
                    v = v[:32_000] + "…"
                vals.append(v)
            sheet.append(vals)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for i, col in enumerate(cols, 1):
            sheet.column_dimensions[get_column_letter(i)].width = min(max(12, len(str(col)) + 2), 48)
    wb.save(path)
    return path
