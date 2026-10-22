"""Turn resolved PI lines into a complete carton plan (full cartons + optimised mixed cartons)."""
from __future__ import annotations

import colorsys
import math
import random
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np

from .engine import (AXIS_NAME, PERMS_ALL, SCALE, Block, CType, Carton, Packer, Sku, S,
                     best_grid_count, expand_boxes, full_carton_layout, optimize, validate_carton)
from .gapfill import compress_boxes, gap_fill

MM_PER_INCH = 25.4


@dataclass
class Settings:
    mode: str = "uniform"            # 'uniform' (all boxes same direction) | 'per_product'
    upright_only: bool = False       # keep box height vertical ("this side up")
    fill_full_cartons: bool = True   # fill full cartons completely to 3D geometric capacity (no empty space)
    carton_tolerance_mm: float = 0.0  # extra usable room added to each carton dimension
    carton_reduction_inch: float = 1.0  # outer carton size (L, W, H) reduced by this many inches for 3D calculation
    allowed_cartons: Optional[list] = None
    min_fill: float = 0.75           # cartons below this fill are flagged
    time_budget: float = 20.0
    workers: Optional[int] = None

    @staticmethod
    def from_dict(d):
        s = Settings()
        for k, v in (d or {}).items():
            if hasattr(s, k) and v is not None:
                setattr(s, k, v)
        return s


def sku_color(i, n=1):
    h = (i * 0.61803398875) % 1.0
    s = 0.55 + 0.25 * ((i * 7) % 3) / 2
    v = 0.85 - 0.15 * ((i * 5) % 3) / 2
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _fmt_in(x):
    return ("%g" % x)


def carton_label_in(c):
    return f"{_fmt_in(c['l_in'])} X {_fmt_in(c['w_in'])} X {_fmt_in(c['h_in'])}"


def orientation_info(sku: Sku, perm_id: int):
    p = PERMS_ALL[perm_id]
    dims = (sku.l, sku.w, sku.h)
    ax = [AXIS_NAME[p[0]], AXIS_NAME[p[1]], AXIS_NAME[p[2]]]
    along = {"x": ax[0], "y": ax[1], "z": ax[2]}
    ext = {"x": dims[p[0]], "y": dims[p[1]], "z": dims[p[2]]}
    if p[2] == 2:
        posture = "upright (as printed, height H is vertical)"
    elif p[2] == 0:
        posture = "stood on its short end - box LENGTH (L) is vertical"
    else:
        posture = "laid on its side - box WIDTH (W) is vertical"
    txt = (f"{ax[0]} along carton LENGTH, {ax[1]} along carton WIDTH, {ax[2]} UP - {posture}")
    return {"perm": perm_id, "along": along, "extent": ext, "text": txt, "posture": posture}


def build_layers(blocks, skus):
    """Layer number = 1 + highest layer of the boxes this box rests on (floor boxes = layer 1)."""
    recs = []   # (block, level, ix, iy, x, y, z, dx, dy, dz)
    for bi, b in enumerate(blocks):
        for k in range(b.nz):
            for iy in range(b.ny):
                for ix in range(b.nx):
                    recs.append((bi, k, ix, iy, b.x + ix * b.dx, b.y + iy * b.dy, b.z + k * b.dz, b.dx, b.dy, b.dz))
    n = len(recs)
    if n == 0:
        return []
    a = np.array([(r[4], r[5], r[6], r[7], r[8], r[9]) for r in recs], dtype=np.int64)
    x0, y0, z0 = a[:, 0], a[:, 1], a[:, 2]
    x1, y1, z1 = x0 + a[:, 3], y0 + a[:, 4], z0 + a[:, 5]
    layer = np.ones(n, dtype=np.int64)
    for i in np.argsort(z0, kind="stable"):
        if z0[i] == 0:
            continue
        below = (z1 == z0[i]) & (x0 < x1[i]) & (x1 > x0[i]) & (y0 < y1[i]) & (y1 > y0[i])
        below[i] = False
        if below.any():
            layer[i] = layer[below].max() + 1
    groups = {}
    for i, r in enumerate(recs):
        groups.setdefault((r[0], r[1]), []).append(i)
    by_layer = {}
    for (bi, k), idxs in groups.items():
        b = blocks[bi]
        lset = {int(layer[i]) for i in idxs}
        if len(lset) == 1:
            L = lset.pop()
            by_layer.setdefault(L, []).append(
                {"block": bi, "sku": b.sku, "x": b.x / SCALE, "y": b.y / SCALE, "z": (b.z + k * b.dz) / SCALE,
                 "nx": b.nx, "ny": b.ny, "dx": b.dx / SCALE, "dy": b.dy / SCALE, "dz": b.dz / SCALE,
                 "perm": b.perm, "count": b.nx * b.ny, "level_in_block": k + 1, "levels_in_block": b.nz})
        else:
            for i in idxs:
                r = recs[i]
                by_layer.setdefault(int(layer[i]), []).append(
                    {"block": bi, "sku": b.sku, "x": r[4] / SCALE, "y": r[5] / SCALE, "z": r[6] / SCALE,
                     "nx": 1, "ny": 1, "dx": b.dx / SCALE, "dy": b.dy / SCALE, "dz": b.dz / SCALE,
                     "perm": b.perm, "count": 1, "level_in_block": k + 1, "levels_in_block": b.nz})
    layers = []
    for L in sorted(by_layer):
        items = sorted(by_layer[L], key=lambda it: (it["y"], it["x"]))
        layers.append({"no": L, "z": min(it["z"] for it in items),
                       "top": max(it["z"] + it["dz"] for it in items),
                       "count": sum(it["count"] for it in items), "items": items})
    return layers


def block_json(b: Block):
    return {"sku": b.sku, "perm": b.perm, "x": b.x / SCALE, "y": b.y / SCALE, "z": b.z / SCALE,
            "nx": b.nx, "ny": b.ny, "nz": b.nz, "dx": b.dx / SCALE, "dy": b.dy / SCALE,
            "dz": b.dz / SCALE, "count": b.count}


def carton_json(c: Carton, skus, spec_by_idx, number=None, sku_map=None):
    blocks = sorted(c.blocks, key=lambda b: (b.z, b.y, b.x))
    layers = build_layers(blocks, skus)
    contents = {}
    for b in blocks:
        e = contents.setdefault(b.sku, {"sku": b.sku, "boxes": 0, "perms": set()})
        e["boxes"] += b.count
        e["perms"].add(b.perm)
    cont = []
    for sk, e in sorted(contents.items(), key=lambda kv: -kv[1]["boxes"]):
        s = skus[sk]
        sp = spec_by_idx[s.idx]
        cont.append({"sku": sk, "code": s.code, "name": s.name, "boxes": e["boxes"],
                     "units_per_inner": s.units_per_inner, "units": e["boxes"] * s.units_per_inner,
                     "dims": [s.l, s.w, s.h],
                     "top_up": getattr(s, "top_up", False),
                     "orientations": [orientation_info(s, p) for p in sorted(e["perms"])]})
    # steps: one per block (bottom to top)
    steps = []
    for si, b in enumerate(blocks, start=1):
        s = skus[b.sku]
        oi = orientation_info(s, b.perm)
        layers_idx = [l["no"] for l in layers
                      if any(it["block"] == si - 1 for it in l["items"])] or [1]
        steps.append({
            "step": si, "block": si - 1, "sku": b.sku, "name": s.name, "code": s.code,
            "layer_from": min(layers_idx), "layer_to": max(layers_idx),
            "grid": f"{b.nx} x {b.ny}" + (f" x {b.nz} layers" if b.nz > 1 else ""),
            "count": b.count,
            "x": b.x / SCALE, "y": b.y / SCALE, "z": b.z / SCALE,
            "orientation": oi["text"],
        })
    X, Y, Z = c.ctype.dims_s
    # largest free height above packed content (informational)
    max_top = max((b.z + b.nz * b.dz) for b in blocks) if blocks else 0
    bj = [block_json(b) for b in blocks]
    if sku_map:
        for x in bj:
            x["sku"] = sku_map[x["sku"]]
        for l in layers:
            for it in l["items"]:
                it["sku"] = sku_map[it["sku"]]
        for st in steps:
            st["sku"] = sku_map[st["sku"]]
        for x in cont:
            x["sku"] = sku_map[x["sku"]]
    actual_l = round(c.ctype.outer_l_in * MM_PER_INCH, 1) if c.ctype.outer_l_in else X / SCALE
    actual_w = round(c.ctype.outer_w_in * MM_PER_INCH, 1) if c.ctype.outer_w_in else Y / SCALE
    actual_h = round(c.ctype.outer_h_in * MM_PER_INCH, 1) if c.ctype.outer_h_in else Z / SCALE
    return {
        "no": number,
        "carton": {"key": c.ctype.key, "name": c.ctype.name,
                   "l": actual_l, "w": actual_w, "h": actual_h,
                   "calc_l": X / SCALE, "calc_w": Y / SCALE, "calc_h": Z / SCALE,
                   "label": c.ctype.label,
                   "outer_label": c.ctype.label,
                   "calc_label": f"{c.ctype.calc_l_in:g} X {c.ctype.calc_w_in:g} X {c.ctype.calc_h_in:g}" if c.ctype.calc_l_in else f"{X/SCALE/MM_PER_INCH:.1f} X {Y/SCALE/MM_PER_INCH:.1f} X {Z/SCALE/MM_PER_INCH:.1f}",
                   "outer_l_in": c.ctype.outer_l_in, "outer_w_in": c.ctype.outer_w_in, "outer_h_in": c.ctype.outer_h_in,
                   "calc_l_in": c.ctype.calc_l_in, "calc_w_in": c.ctype.calc_w_in, "calc_h_in": c.ctype.calc_h_in},
        "fill": round(c.fill, 4),
        "boxes": sum(x["boxes"] for x in cont),
        "units": sum(x["units"] for x in cont),
        "contents": cont,
        "blocks": bj,
        "layers": layers,
        "steps": steps,
        "max_height_used": max_top / SCALE,
    }


def plan(specs, carton_specs, settings: Settings, progress=None):
    """
    specs: list of dicts: idx, code, name, boxes, upb, l, w, h (packing dims, mm, margin incl.),
           per_carton (int|None), carton_key (str|None), source
    carton_specs: dict key -> {key,name,l_in,w_in,h_in}
    """
    t0 = time.time()
    warnings = []
    tol = settings.carton_tolerance_mm
    red_in = settings.carton_reduction_inch
    all_ct = {}
    for key, c in carton_specs.items():
        l_in_outer = float(c["l_in"])
        w_in_outer = float(c["w_in"])
        h_in_outer = float(c["h_in"])
        calc_l_in = max(0.1, round(l_in_outer - red_in, 2))
        calc_w_in = max(0.1, round(w_in_outer - red_in, 2))
        calc_h_in = max(0.1, round(h_in_outer - red_in, 2))
        l, w, h = calc_l_in * MM_PER_INCH, calc_w_in * MM_PER_INCH, calc_h_in * MM_PER_INCH
        all_ct[key] = CType(key=key, name=c["name"], l=round(l + tol, 1), w=round(w + tol, 1), h=round(h + tol, 1),
                            label=carton_label_in(c),
                            outer_l_in=l_in_outer, outer_w_in=w_in_outer, outer_h_in=h_in_outer,
                            calc_l_in=calc_l_in, calc_w_in=calc_w_in, calc_h_in=calc_h_in)
    allowed_keys = settings.allowed_cartons or list(all_ct.keys())
    allowed = [all_ct[k] for k in allowed_keys if k in all_ct]
    if not allowed:
        raise ValueError("No carton type selected.")

    spec_by_idx = {s["idx"]: s for s in specs}
    skus_all = []
    full_rows = []
    mixed_skus = []
    excluded = []
    for sp in specs:
        try:
            if not (sp["l"] > 0 and sp["w"] > 0 and sp["h"] > 0 and sp["boxes"] > 0):
                raise ValueError
        except Exception:
            excluded.append({"idx": sp.get("idx"), "name": sp.get("name", "?"), "reason": "ignored - invalid box size or quantity"})
            continue
        sku = Sku(idx=sp["idx"], key=str(sp["idx"]), code=sp["code"], name=sp["name"],
                  l=sp["l"], w=sp["w"], h=sp["h"], qty=sp["boxes"], units_per_inner=sp["upb"],
                  top_up=bool(sp.get("top_up")))
        # per-carton capacity
        home = all_ct.get(sp.get("carton_key") or "")
        if home is None:
            # choose carton type with best single-orientation capacity
            best = None
            for ct in allowed:
                g, _ = best_grid_count(sku, ct, settings.upright_only)
                if best is None or g > best[0] or (g == best[0] and ct.vol < best[1].vol):
                    best = (g, ct)
            home = best[1]

        geom_cap, _ = best_grid_count(sku, home, settings.upright_only)
        if geom_cap <= 0:
            # If 1-inch reduction made carton smaller than box, check actual outer carton dimensions
            outer_ct = CType(key=home.key, name=home.name,
                             l=round((home.outer_l_in or (home.l/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             w=round((home.outer_w_in or (home.w/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             h=round((home.outer_h_in or (home.h/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             label=home.label)
            outer_cap, _ = best_grid_count(sku, outer_ct, settings.upright_only)
            if outer_cap > 0:
                geom_cap = outer_cap

        if settings.fill_full_cartons:
            per = geom_cap
        else:
            per = sp.get("per_carton") or geom_cap

        if per <= 0:
            excluded.append({"idx": sp["idx"], "name": sp["name"], "reason": "inner box does not fit in the carton"})
            continue
        n_full, rem = divmod(sp["boxes"], per)
        skus_all.append({"sku": sku, "home": home, "per": per, "n_full": n_full, "rem": rem, "spec": sp})

    # ---------------- full cartons
    full = []
    carton_no = 1
    layout_cache = {}
    for e in skus_all:
        if e["n_full"] <= 0:
            continue
        sku, home, per = e["sku"], e["home"], e["per"]
        ck = (sku.l, sku.w, sku.h, home.key, per, settings.upright_only or sku.top_up)
        if ck not in layout_cache:
            layout_cache[ck] = full_carton_layout(sku, home, per, mode="per_product", upright_only=settings.upright_only or sku.top_up)
        lay, got = layout_cache[ck]
        if got < per:
            # If 1-inch reduction made layout tight, attempt layout with actual outer carton dimensions
            outer_ct = CType(key=home.key, name=home.name,
                             l=round((home.outer_l_in or (home.l/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             w=round((home.outer_w_in or (home.w/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             h=round((home.outer_h_in or (home.h/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                             label=home.label)
            lay_outer, got_outer = full_carton_layout(sku, outer_ct, per, mode="per_product", upright_only=settings.upright_only or sku.top_up)
            if got_outer > got:
                lay, got = lay_outer, got_outer
                layout_cache[ck] = (lay, got)

        note = ""
        master_per = e["spec"].get("per_carton")
        if got < per:
            if not e["spec"].get("source", "").startswith("master"):
                note = (f"Target capacity is {per} inner boxes per carton, but the 3D model fits only {got}. "
                        f"Layout shows {got}; verify before packing.")
                warnings.append(f"{sku.name}: {note}")
        elif master_per and master_per != per:
            if per > master_per:
                note = (f"Carton is perfectly filled to 100% full 3D capacity ({per} inner boxes per carton; "
                        f"master sheet listed {master_per}). Any remaining inner boxes are added to mixed cartons.")
            else:
                note = (f"Packed to physical 3D capacity ({per} inner boxes per carton; "
                        f"master sheet listed {master_per}, but only {per} physically fit).")
        if sku.top_up:
            note = (note + " " if note else "") + "Top side facing upward (Versa patch)."
        # carton json (layout uses a single-sku local index 0)
        one = Sku(0, sku.key, sku.code, sku.name, sku.l, sku.w, sku.h, per, sku.units_per_inner, top_up=sku.top_up)
        spec_local = {0: e["spec"]}
        cj = carton_json(lay, [one], spec_local, sku_map={0: sku.idx})
        a, b = carton_no, carton_no + e["n_full"] - 1
        carton_no += e["n_full"]
        full.append({
            "sku": sku.idx, "code": sku.code, "name": sku.name, "carton_from": a, "carton_to": b,
            "n": e["n_full"], "boxes_per_carton": per, "units_per_inner": sku.units_per_inner,
            "units_per_carton": per * sku.units_per_inner,
            "top_up": sku.top_up,
            "carton": cj["carton"], "carton_label_in": carton_label_in(carton_specs[home.key]),
            "layout": {"blocks": cj["blocks"], "layers": cj["layers"], "steps": cj["steps"],
                       "contents": cj["contents"], "fill": cj["fill"], "achieved": got},
            "note": note,
        })
        if e["rem"]:
            mixed_skus.append(e)
        # (full-carton SKUs without remainder are not added to mixed pool)
    for e in skus_all:
        if e["n_full"] <= 0 and e["rem"]:
            mixed_skus.append(e)

    # ---------------- mixed cartons
    mixed_json = []
    mixed_objs = []
    mixed_stats = {}
    pool_skus = []
    for e in mixed_skus:
        s = e["sku"]
        pool_skus.append(Sku(len(pool_skus), s.key, s.code, s.name, s.l, s.w, s.h, e["rem"], s.units_per_inner,
                             top_up=s.top_up))
        pool_skus[-1].idx = len(pool_skus) - 1
    pool_map = {i: mixed_skus[i]["sku"].idx for i in range(len(pool_skus))}   # pool index -> spec idx

    # make sure every pool sku fits some allowed carton, otherwise add its home carton
    ct_for_mixed = list(allowed)
    for i, s in enumerate(pool_skus):
        fits = any(best_grid_count(s, ct, settings.upright_only)[0] > 0 for ct in ct_for_mixed)
        if not fits:
            home = mixed_skus[i]["home"]
            if best_grid_count(s, home, settings.upright_only)[0] > 0 and home not in ct_for_mixed:
                ct_for_mixed.append(home)
                warnings.append(f"{s.name}: does not fit the selected cartons - its own carton {home.name} was added for the mixed packing.")
            elif best_grid_count(s, home, settings.upright_only)[0] <= 0:
                outer_ct = CType(key=home.key, name=home.name,
                                 l=round((home.outer_l_in or (home.l/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                                 w=round((home.outer_w_in or (home.w/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                                 h=round((home.outer_h_in or (home.h/SCALE/MM_PER_INCH)) * MM_PER_INCH + tol, 1),
                                 label=home.label)
                if best_grid_count(s, outer_ct, settings.upright_only)[0] > 0:
                    ct_for_mixed.append(outer_ct)
                else:
                    excluded.append({"idx": mixed_skus[i]["sku"].idx, "name": s.name, "reason": "leftover box does not fit any carton"})
                    s.qty = 0

    lower_bound = None
    if pool_skus:
        pool_vol = sum(s.qty * S(s.l) * S(s.w) * S(s.h) for s in pool_skus)
        lower_bound = pool_vol / max(c.vol for c in ct_for_mixed)
        pk = Packer(pool_skus, ct_for_mixed, mode=settings.mode, upright_only=settings.upright_only)
        try:
            pk, cartons = optimize(pool_skus, ct_for_mixed, mode=settings.mode, upright_only=settings.upright_only,
                                   time_budget=settings.time_budget, workers=settings.workers,
                                   min_fill=settings.min_fill, progress=progress)
            item_vol = pk.item_vol
            rng = random.Random(99)
            try:
                cartons = gap_fill(cartons, pool_skus, settings.mode, settings.upright_only, item_vol,
                                   deadline=time.time() + max(10, settings.time_budget))
                small_first = sorted(ct_for_mixed, key=lambda c: c.vol)
                for i, c in enumerate(cartons):
                    pool = pk._qty_of([c])
                    for ct in small_first:
                        if ct.vol >= c.ctype.vol:
                            break
                        r = pk.solve_pool(pool, rng, tries=10, ctypes=[ct], max_cartons=1)
                        if r:
                            cartons[i] = r[0]
                            break
                cartons = gap_fill(cartons, pool_skus, settings.mode, settings.upright_only, item_vol,
                                   deadline=time.time() + 10)
            except Exception as ex:      # optimisation extras failed - keep the plain solution
                warnings.append(f"Final space optimisation skipped ({ex}); plain packing used.")
            for c in cartons:
                if validate_carton(c, pool_skus):
                    raise RuntimeError("validation")
        except Exception:
            # safe fallback: simple sequential packing
            pk = Packer(pool_skus, ct_for_mixed, mode=settings.mode, upright_only=settings.upright_only)
            cartons = pk.solve_pool(pk.qty0, random.Random(5), tries=4) or []
            warnings.append("Optimised packing could not be completed; a simpler packing was used (more cartons possible).")
        item_vol = pk.item_vol
        cartons.sort(key=lambda c: -c.fill)
        for c in cartons:
            probs = validate_carton(c, pool_skus)
            if probs:
                raise RuntimeError("internal validation failed: " + "; ".join(probs))
        # verify counts
        got = {}
        for c in cartons:
            for sk, q in c.qty_by_sku().items():
                got[sk] = got.get(sk, 0) + q
        for i, s in enumerate(pool_skus):
            if got.get(i, 0) != s.qty:
                raise RuntimeError(f"count mismatch for {s.name}: packed {got.get(i, 0)} of {s.qty}")
        # remap sku idx in blocks to spec idx
        for c in cartons:
            for b in c.blocks:
                pass
        for n, c in enumerate(cartons):
            cj = carton_json(c, pool_skus, {i: mixed_skus[i]["spec"] for i in range(len(pool_skus))},
                             number=carton_no + n, sku_map=pool_map)
            cj["low_fill"] = c.fill < settings.min_fill
            # translate sku indexes to spec idx for the client
            mixed_json.append(cj)
            if cj["low_fill"]:
                warnings.append(f"Mixed carton {cj['no']} is only {cj['fill']*100:.0f}% full - no other leftover box fits into the remaining space.")
        carton_no += len(cartons)

    # ---------------- packing list rows (same layout as the 'Output Want' sheet)
    rows = []
    for f in full:
        no = f"{f['carton_from']} TO {f['carton_to']}" if f["n"] > 1 else f"{f['carton_from']}"
        rows.append({"code": f["code"], "name": f["name"], "carton_nos": no,
                     "formula": f"{f['units_per_inner']}X{f['boxes_per_carton']}X{f['n']}",
                     "units": f["units_per_inner"] * f["boxes_per_carton"] * f["n"],
                     "dimension": f["carton_label_in"], "type": "FULL"})
    for m in mixed_json:
        first = True
        for cnt in m["contents"]:
            rows.append({"code": cnt["code"], "name": cnt["name"],
                         "carton_nos": str(m["no"]) if first else "",
                         "formula": f"{cnt['units_per_inner']}X{cnt['boxes']}X1",
                         "units": cnt["units"],
                         "dimension": m["carton"]["label"] if first else "", "type": "MIXED",
                         "mixed_no": m["no"]})
            first = False

    # SKU table
    sku_table = []
    for e in skus_all:
        sp = e["spec"]
        sku_table.append({"idx": sp["idx"], "code": sp["code"], "name": sp["name"], "boxes": sp["boxes"],
                          "units_per_inner": sp["upb"], "per_carton": e["per"], "n_full": e["n_full"],
                          "remainder": e["rem"], "dims": [e["sku"].l, e["sku"].w, e["sku"].h],
                          "home_carton": e["home"].name, "color": sku_color(sp["idx"]),
                          "top_up": bool(sp.get("top_up")),
                          "source": sp.get("source", "")})

    tot_mixed_vol = sum(m["carton"]["l"] * m["carton"]["w"] * m["carton"]["h"] for m in mixed_json)
    packed_mixed_vol = sum(m["fill"] * m["carton"]["l"] * m["carton"]["w"] * m["carton"]["h"] for m in mixed_json)
    summary = {
        "full_cartons": sum(f["n"] for f in full),
        "mixed_cartons": len(mixed_json),
        "total_cartons": sum(f["n"] for f in full) + len(mixed_json),
        "mixed_boxes": sum(m["boxes"] for m in mixed_json),
        "mixed_units": sum(m["units"] for m in mixed_json),
        "avg_mixed_fill": (packed_mixed_vol / tot_mixed_vol) if tot_mixed_vol else None,
        "mixed_lower_bound": math.ceil(lower_bound - 1e-9) if lower_bound else 0,
        "mixed_products": len(pool_skus),
        "low_fill_cartons": sum(1 for m in mixed_json if m.get("low_fill")),
        "seconds": round(time.time() - t0, 1),
    }
    return {
        "summary": summary,
        "settings": asdict(settings),
        "skus": sku_table,
        "full": full,
        "mixed": mixed_json,
        "packing_list": rows,
        "warnings": warnings,
        "excluded": excluded,
        "allowed_cartons": [c.name for c in ct_for_mixed],
    }
