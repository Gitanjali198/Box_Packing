"""Match PI lines to master items (code -> exact name -> fuzzy) and record confidence."""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .master import Master, MasterItem, norm


@dataclass
class Match:
    status: str                  # exact_code | exact_name | fuzzy | manual | unmatched
    item: Optional[MasterItem]
    candidates: list = field(default_factory=list)   # alternative MasterItems
    note: str = ""


def _strip_paren(s: str) -> str:
    return re.sub(r"\(.*?\)", " ", s or "")


def _name_variants(name: str):
    out = []
    for v in (name, _strip_paren(name)):
        n = norm(v)
        if n:
            out.append(n)
    # "Patch Bullseye 2 Dual Cure 20" -> drop leading word PATCH
    n = norm(re.sub(r"^\s*patch\s+", "", _strip_paren(name), flags=re.I))
    if n:
        out.append(n)
    # drop trailing "Box of N" and similar noise
    n = norm(re.sub(r",?\s*box of \d+", "", _strip_paren(name), flags=re.I))
    if n:
        out.append(n)
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _pick(cands, upb):
    """Pick the best candidate among duplicates: prefer matching units-per-inner-box, then having dims."""
    if not cands:
        return None
    def score(it: MasterItem):
        s = 0
        if upb and it.units_per_inner == upb:
            s += 4
        if it.has_dims:
            s += 2
        return (-s, it.row)
    return sorted(cands, key=score)[0]


class Matcher:
    def __init__(self, master: Master, alias_path: Optional[str] = None):
        self.master = master
        self.alias_path = alias_path
        self.aliases = {}
        if alias_path and os.path.exists(alias_path):
            try:
                with open(alias_path, "r", encoding="utf-8") as f:
                    self.aliases = json.load(f)
            except Exception:
                self.aliases = {}
        self._all_names = {}
        for it in master.items:
            for v in _name_variants(it.name):
                self._all_names.setdefault(v, []).append(it)

    # ---- persistence of manual choices ------------------------------------------------
    def remember(self, line, item: MasterItem):
        key = norm(line.code) + "|" + norm(line.name)
        self.aliases[key] = item.row
        if self.alias_path:
            with open(self.alias_path, "w", encoding="utf-8") as f:
                json.dump(self.aliases, f, indent=1)

    def match(self, line) -> Match:
        m = self.master
        upb = line.units_per_box
        akey = norm(line.code) + "|" + norm(line.name)
        if akey in self.aliases:
            row = self.aliases[akey]
            for it in m.items:
                if it.row == row:
                    return Match("manual", it, note="saved mapping")

        # 1. exact code
        if line.code:
            c = m.by_code.get(norm(line.code), [])
            if c:
                best = _pick(c, upb)
                note = ""
                if best.units_per_inner and best.units_per_inner != upb:
                    note = f"PI qty/box={upb} but master inner box holds {best.units_per_inner}"
                return Match("exact_code", best, [x for x in c if x is not best], note)

        # 2. exact name (code col, name col)
        keys = [line.name, line.code]
        for k in keys:
            for v in _name_variants(k or ""):
                c = self._all_names.get(v, [])
                if c:
                    same_upb = [x for x in c if x.units_per_inner == upb]
                    pool = same_upb or c
                    best = _pick(pool, upb)
                    note = ""
                    if best.units_per_inner and best.units_per_inner != upb:
                        note = f"PI qty/box={upb} but master inner box holds {best.units_per_inner}"
                    status = "exact_name" if (same_upb or not upb) else "fuzzy"
                    if note:
                        status = "fuzzy"
                    return Match(status, best, [x for x in c if x is not best], note)

        # 3. fuzzy on names
        names = list(self._all_names.keys())
        for k in (line.name, _strip_paren(line.name), line.code):
            nk = norm(k)
            if not nk:
                continue
            close = difflib.get_close_matches(nk, names, n=4, cutoff=0.82)
            if close:
                cands = []
                for cl in close:
                    cands.extend(self._all_names[cl])
                best = _pick(cands, upb)
                return Match("fuzzy", best, [x for x in cands if x is not best],
                             "approximate name match - please verify")
        return Match("unmatched", None, [], "not found in master data")
