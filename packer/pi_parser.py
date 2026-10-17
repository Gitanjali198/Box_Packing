"""Parse a Proforma Invoice worksheet into order lines (robust to the many PI layouts)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import openpyxl

from .master import to_float


@dataclass
class PILine:
    idx: int
    row: int
    code: str
    name: str
    extra: str            # size / dimension text found on the row
    units_per_box: Optional[int]
    boxes: int
    units: Optional[int]
    keys: list = field(default_factory=list)   # candidate lookup strings
    highlighted: bool = False                  # row filled yellow in the PI

    def to_dict(self):
        return asdict(self)


@dataclass
class PIDoc:
    sheet: str
    title: str
    consignee: str
    lines: list
    has_highlight: bool = False


# header keyword patterns -> column role
_ROLES = [
    ("units_total", re.compile(r"(TOTAL\s*(NO\.?\s*OF\s*)?UNITS?|NO\.?\s*OF\.?\s*UNITS?|TOTAL\s*UNITS|TOTAL\s*QTY)", re.I)),
    ("boxes", re.compile(r"(TOTAL\s*ORDER\s*BOX|TOTAL\s*BOXES|NO\.?\s*OF\.?\s*BOX|NO\s*OF\s*BOXES|ORDER\s*BOX)", re.I)),
    ("upb", re.compile(r"(QTY\s*(PER|/)\s*BOX|QTY\s*BOX|UNITS?\s*PER\s*BOX|QTY\s*/\s*BOX|QTY\s+BOX)", re.I)),
    ("code", re.compile(r"(PRODUCT\s*CODE|ITEM\s*CODE)", re.I)),
    ("desc", re.compile(r"(DESCRIPTION|^PRODUCT$|^ITEM$)", re.I)),
    ("size", re.compile(r"(DIMENSION|OUR\s*SIZE|SIZE)", re.I)),
    ("sno", re.compile(r"(S\.?\s*NO|SL\.?\s*NO)", re.I)),
]


def _cell_text(v) -> str:
    return "" if v is None else str(v).strip()


def _find_header(rows):
    """Return (row_index, {role: col_index}) of the product table header."""
    best = None
    for i, row in enumerate(rows[:60]):
        roles = {}
        for c, v in enumerate(row):
            t = _cell_text(v)
            if not t:
                continue
            for role, pat in _ROLES:
                if role in roles:
                    continue
                if pat.search(t):
                    # avoid mis-assigning: a header like 'TOTAL ORDER BOXES' must not become upb
                    roles[role] = c
                    break
        score = sum(1 for r in ("boxes", "upb", "desc") if r in roles)
        if "boxes" in roles and ("upb" in roles or "units_total" in roles):
            score += 2
        if score >= 3 and (best is None or score > best[0]):
            best = (score, i, roles)
            break
    if not best:
        return None
    return best[1], best[2]


def list_sheets(path: str) -> list:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=60, values_only=True)):
            rows.append(row)
        h = _find_header(rows)
        title = ""
        for row in rows[:20]:
            for v in row:
                t = _cell_text(v)
                if re.search(r"(PROFORMA\s*INVOICE\s*NO|PI\s*NO)", t, re.I):
                    title = re.sub(r"\s+", " ", t)
                    break
            if title:
                break
        out.append({"sheet": ws.title, "is_pi": h is not None, "title": title})
    return out


def is_yellow_rgb(rgb) -> bool:
    """True for the yellow family used for highlighting (FFFF00, FFFF99, FFF200, FFEB9C ...)."""
    if not isinstance(rgb, str) or len(rgb) not in (6, 8):
        return False
    h = rgb[-6:]
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return False
    return r >= 0xE8 and g >= 0xD8 and b <= 0xB0 and (r - b) >= 0x40


def _cell_yellow(c) -> bool:
    try:
        fl = c.fill
        if fl is None or fl.fill_type != "solid":
            return False
        col = fl.fgColor
        if col.type == "rgb":
            return is_yellow_rgb(col.rgb)
        if col.type == "indexed":
            return col.indexed in (5, 13, 43)
    except Exception:
        pass
    return False


def _load_rows(path: str, sheet: Optional[str]):
    """Returns (sheet title, rows of values, rows of yellow flags)."""
    if path.lower().endswith(".xls"):
        import pandas as pd
        df = pd.read_excel(path, sheet_name=sheet or 0, header=None)
        rows = [tuple(None if (isinstance(x, float) and x != x) else x for x in r) for r in df.values.tolist()]
        yel = [[False] * len(r) for r in rows]
        try:
            import xlrd
            wb = xlrd.open_workbook(path, formatting_info=True)
            sh = wb.sheet_by_name(sheet) if sheet else wb.sheet_by_index(0)
            for r in range(min(sh.nrows, len(rows))):
                for c in range(sh.ncols):
                    xf = wb.xf_list[sh.cell_xf_index(r, c)]
                    pat = xf.background.pattern_colour_index
                    rgb = wb.colour_map.get(pat)
                    if rgb and xf.background.fill_pattern == 1 and is_yellow_rgb("%02X%02X%02X" % rgb):
                        if c < len(yel[r]):
                            yel[r][c] = True
        except Exception:
            pass
        return sheet or "Sheet1", rows, yel
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
    rows, yel = [], []
    for row in ws.iter_rows():
        rows.append(tuple(getattr(c, "value", None) for c in row))
        yel.append([_cell_yellow(c) for c in row])
    return ws.title, rows, yel


def parse_pi(path: str, sheet: Optional[str] = None) -> PIDoc:
    title_sheet, rows, yel = _load_rows(path, sheet)
    hdr = _find_header(rows)
    if not hdr:
        raise ValueError("Could not find a product table (need columns like Product / Qty per box / Total boxes).")
    hi, roles = hdr

    title = ""
    consignee = ""
    for row in rows[:25]:
        for c, v in enumerate(row):
            t = _cell_text(v)
            if not title and re.search(r"(PROFORMA\s*INVOICE\s*NO|PI\s*NO)", t, re.I):
                title = re.sub(r"\s+", " ", t)
            if t.upper().startswith("CONSIGNEE") or t.upper().startswith("BUYER"):
                # consignee name is usually in the row below
                pass
    # consignee: first non-empty text two rows after 'CONSIGNEE'/'BUYER'
    for ri, row in enumerate(rows[:12]):
        for c, v in enumerate(row):
            t = _cell_text(v).upper()
            if t in ("CONSIGNEE", "CONSIGNEE:", "BUYER"):
                for rj in range(ri + 1, min(ri + 4, len(rows))):
                    if c < len(rows[rj]) and _cell_text(rows[rj][c]):
                        consignee = _cell_text(rows[rj][c])
                        break
                break
        if consignee:
            break

    lines = []
    desc_c = roles.get("desc")
    code_c = roles.get("code")
    sno_c = roles.get("sno")
    size_c = roles.get("size")
    upb_c, box_c, uni_c = roles.get("upb"), roles.get("boxes"), roles.get("units_total")

    # Some PIs (e.g. Pumalas) have no 'description' header but 'Product'.
    for ri in range(hi + 1, len(rows)):
        row = rows[ri]

        def g(c):
            return row[c] if (c is not None and c < len(row)) else None

        boxes = to_float(g(box_c))
        upb = to_float(g(upb_c))
        units = to_float(g(uni_c))
        txt_all = " ".join(_cell_text(x) for x in row).upper()
        if re.search(r"\bTOTAL\s*(IN\s*)?(USD|US\$|VALUE)", txt_all) or "PAYMENT TERMS" in txt_all:
            if boxes is None:
                # table finished
                if any(l for l in lines):
                    break
                continue
        if boxes is None or boxes <= 0:
            continue
        if upb is None and units is not None and boxes:
            upb = units / boxes
        if upb is None or upb <= 0:
            continue
        name = _cell_text(g(desc_c))
        code = _cell_text(g(code_c))
        sno = _cell_text(g(sno_c))
        size = _cell_text(g(size_c))
        if isinstance(g(code_c), float) and float(g(code_c)).is_integer():
            code = str(int(g(code_c)))
        # Layouts where the S.NO column actually carries the item name/code (Kores, Pumalas)
        if not code and sno and not re.fullmatch(r"\d+(\.0)?", sno):
            code = sno
        if not name:
            name = code
        if not name:
            continue
        keys = [k for k in (code, name, size) if k]
        yrow = yel[ri] if ri < len(yel) else []
        key_cols = [c for c in (desc_c, code_c, sno_c, box_c, upb_c) if c is not None]
        hl = any(c < len(yrow) and yrow[c] for c in key_cols)
        lines.append(PILine(idx=len(lines), row=ri + 1, code=code, name=name,
                            extra=size, units_per_box=int(round(upb)), boxes=int(round(boxes)),
                            units=int(round(units)) if units else int(round(upb * boxes)), keys=keys, highlighted=hl))
    if not lines:
        raise ValueError("No product lines found in the selected sheet.")
    return PIDoc(sheet=title_sheet, title=title, consignee=consignee, lines=lines,
                 has_highlight=any(l.highlighted for l in lines))
