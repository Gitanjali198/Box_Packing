"""
3D mixed-carton packing engine.

Model
-----
* Every inner box is an axis-aligned cuboid (L, W, H in mm, margin already included).
* Boxes are never tilted to an arbitrary angle: a box is placed in one of at most six
  axis-aligned orientations ("permutations").  Two orientation policies are supported:

    uniform      every inner box in a carton uses the SAME orientation
                 (box-L along carton length, box-W along carton width, box-H up, or one of
                 the other five axis permutations - chosen per carton).
    per_product  all boxes of one product inside a carton share one orientation, different
                 products may use different orientations.

* Space is managed with guillotine cuboid splitting and *blocks*: a block is an
  nx x ny x nz grid of identical boxes of one product in one orientation.  Because the
  space above a block is limited to the block footprint, every box always rests flat on
  boxes (or the carton floor) beneath it - the result is physically stackable and maps
  naturally to layer-by-layer instructions.

* Cartons are filled one by one (best-fill carton type each time) with randomised
  multi-start, followed by a "re-solve the weakest cartons" local search that removes
  cartons, down-sizes cartons and balances fill.

All coordinates are integers in 0.1 mm units internally (SCALE = 10).
"""
from __future__ import annotations

import itertools
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

SCALE = 10
PERMS_ALL = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
AXIS_NAME = ["L", "W", "H"]


def S(v):
    return int(round(v * SCALE))


@dataclass
class Sku:
    idx: int
    key: str
    code: str
    name: str
    l: float
    w: float
    h: float
    qty: int
    units_per_inner: int = 0
    top_up: bool = False

    @property
    def dims_s(self):
        return (S(self.l), S(self.w), S(self.h))


@dataclass
class CType:
    key: str
    name: str
    l: float
    w: float
    h: float
    label: str = ""
    outer_l_in: float = 0.0
    outer_w_in: float = 0.0
    outer_h_in: float = 0.0
    calc_l_in: float = 0.0
    calc_w_in: float = 0.0
    calc_h_in: float = 0.0

    @property
    def dims_s(self):
        return (S(self.l), S(self.w), S(self.h))

    @property
    def vol(self):
        a, b, c = self.dims_s
        return a * b * c


@dataclass
class Block:
    sku: int
    perm: int          # index into PERMS_ALL
    x: int
    y: int
    z: int
    nx: int
    ny: int
    nz: int
    dx: int            # box extent along carton X/Y/Z for this orientation
    dy: int
    dz: int

    @property
    def count(self):
        return self.nx * self.ny * self.nz

    def boxes(self):
        for iz in range(self.nz):
            for iy in range(self.ny):
                for ix in range(self.nx):
                    yield (self.x + ix * self.dx, self.y + iy * self.dy, self.z + iz * self.dz)


@dataclass
class Carton:
    ctype: CType
    blocks: list
    packed_vol: int = 0

    @property
    def fill(self):
        return self.packed_vol / self.ctype.vol

    def qty_by_sku(self):
        out = {}
        for b in self.blocks:
            out[b.sku] = out.get(b.sku, 0) + b.count
        return out


# ------------------------------------------------------------------------------------
def _shape(m, qty, order):
    """Choose block counts (nx,ny,nz) <= m (max per axis) with product <= qty."""
    a, b, c = order
    n = [1, 1, 1]
    if qty <= m[a]:
        n[a] = qty
        return n
    n[a] = m[a]
    rows = qty // m[a]
    if rows <= m[b]:
        n[b] = rows
        return n
    n[b] = m[b]
    layers = qty // (m[a] * m[b])
    n[c] = min(m[c], layers)
    return n


class Packer:
    """Holds the problem (SKUs, carton types, policy) and exposes the search routines."""

    def __init__(self, skus, ctypes, mode="uniform", upright_only=False):
        self.skus = skus
        self.ctypes = ctypes
        self.mode = mode                # 'uniform' | 'per_product'
        self.upright_only = upright_only
        self.n = len(skus)
        perms = [i for i, p in enumerate(PERMS_ALL) if (not upright_only or p[2] == 2)]
        self.perm_ids = perms
        dims = np.array([s.dims_s for s in skus], dtype=np.int64) if skus else np.zeros((0, 3), dtype=np.int64)
        self.dims = dims
        self.item_vol = (dims[:, 0] * dims[:, 1] * dims[:, 2]).astype(np.float64) if skus else np.zeros(0)
        cs, cp, cA, cB, cC = [], [], [], [], []
        for i in range(self.n):
            sku_upright = self.upright_only or getattr(skus[i], "top_up", False)
            for pid, p in enumerate(PERMS_ALL):
                if sku_upright and p[2] != 2:
                    continue
                cs.append(i)
                cp.append(pid)
                cA.append(dims[i][p[0]])
                cB.append(dims[i][p[1]])
                cC.append(dims[i][p[2]])
        self.c_sku = np.array(cs, dtype=np.int64)
        self.c_perm = np.array(cp, dtype=np.int64)
        self.cA = np.array(cA, dtype=np.int64)
        self.cB = np.array(cB, dtype=np.int64)
        self.cC = np.array(cC, dtype=np.int64)
        self.c_vol = self.item_vol[self.c_sku] if len(cs) else np.zeros(0)
        self.qty0 = np.array([s.qty for s in skus], dtype=np.int64)

    # ------------------------------------------------------------------ one carton
    def pack_carton(self, ctype: CType, qty: np.ndarray, rng: random.Random, noise=0.25) -> Carton:
        X, Y, Z = ctype.dims_s
        qty = qty.copy()
        np_rng = np.random.default_rng(rng.getrandbits(32))
        spaces = [(0, 0, 0, X, Y, Z)]
        lock = np.full(self.n, -1, dtype=np.int64)
        glock = -1
        blocks = []
        packed = 0
        cA, cB, cC = self.cA, self.cB, self.cC
        c_sku, c_perm, c_vol = self.c_sku, self.c_perm, self.c_vol
        K = len(c_sku)
        if K == 0:
            return Carton(ctype, [], 0)

        while spaces:
            Sa = np.array(spaces, dtype=np.int64)
            sx = Sa[:, 3:4]
            sy = Sa[:, 4:5]
            sz = Sa[:, 5:6]
            mx = sx // cA[None, :]
            my = sy // cB[None, :]
            mz = sz // cC[None, :]
            cap = mx * my * mz
            q = qty[c_sku]
            ok = (cap > 0) & (q[None, :] > 0)
            if self.mode == "per_product":
                ok &= ((lock[c_sku] < 0) | (lock[c_sku] == c_perm))[None, :]
            elif glock >= 0:
                ok &= (c_perm == glock)[None, :]
            alive = ok.any(axis=1)
            if not alive.all():
                spaces = [s for s, a in zip(spaces, alive) if a]
                if not spaces:
                    break
                Sa = Sa[alive]
                mx, my, mz, cap, ok = mx[alive], my[alive], mz[alive], cap[alive], ok[alive]
            npl = np.minimum(cap, q[None, :])
            score = npl * c_vol[None, :]
            sv = (Sa[:, 3] * Sa[:, 4] * Sa[:, 5]).astype(np.float64)[:, None]
            # prefer exact/tight fits: fraction of the space volume consumed
            score = score * (1.0 + 0.35 * (score / sv))
            if noise > 0:
                score = score * (1.0 + noise * np_rng.random(score.shape))
            score = np.where(ok, score, -1.0)
            flat = score.ravel()
            # roulette among top 3
            top = np.argpartition(flat, -min(3, flat.size))[-min(3, flat.size):]
            top = [t for t in top if flat[t] > 0]
            if not top:
                break
            top.sort(key=lambda t: -flat[t])
            pick = top[0] if (len(top) == 1 or rng.random() < 0.7) else rng.choice(top[1:])
            si, ki = divmod(int(pick), K)
            x, y, z, ssx, ssy, ssz = (int(v) for v in Sa[si])
            sku = int(c_sku[ki])
            pid = int(c_perm[ki])
            a, b, c = int(cA[ki]), int(cB[ki]), int(cC[ki])
            m = [int(mx[si, ki]), int(my[si, ki]), int(mz[si, ki])]
            order = rng.choice(PERMS_ALL) if rng.random() < 0.6 else (0, 1, 2)
            nx, ny, nz = _shape(m, int(qty[sku]), order)
            bx, by, bz = nx * a, ny * b, nz * c
            blocks.append(Block(sku, pid, x, y, z, nx, ny, nz, a, b, c))
            cnt = nx * ny * nz
            qty[sku] -= cnt
            packed += cnt * int(self.item_vol[sku])
            if self.mode == "per_product":
                lock[sku] = pid
            else:
                glock = pid
            # guillotine split: remove used space, add residuals
            # `spaces` was already filtered to live ones above, so si indexes it directly
            new = [s for j, s in enumerate(spaces) if j != si]
            up = (x, y, z + bz, bx, by, ssz - bz)
            if rng.random() < 0.5:
                bxs = (x + bx, y, z, ssx - bx, by, ssz)
                bys = (x, y + by, z, ssx, ssy - by, ssz)
            else:
                bys = (x, y + by, z, bx, ssy - by, ssz)
                bxs = (x + bx, y, z, ssx - bx, ssy, ssz)
            for r in (up, bxs, bys):
                if r[3] > 0 and r[4] > 0 and r[5] > 0:
                    new.append(r)
            spaces = new
        return Carton(ctype, blocks, packed)

    # ------------------------------------------------------------------ sequential solve
    def _pick_type(self, results, remaining_vol):
        """results: list of Carton (one per type, best of tries)."""
        complete = [r for r in results if r.packed_vol >= remaining_vol]
        if complete:
            return min(complete, key=lambda r: r.ctype.vol)
        # score: packed volume minus air
        def sc(r):
            return 2 * r.packed_vol - r.ctype.vol
        return max(results, key=sc)

    def solve_pool(self, qty: np.ndarray, rng: random.Random, tries=4, ctypes=None, max_cartons=None):
        ctypes = ctypes or self.ctypes
        qty = qty.copy()
        cartons = []
        guard = 0
        while qty.sum() > 0:
            guard += 1
            if guard > 5000 or (max_cartons and len(cartons) >= max_cartons):
                return None if max_cartons else cartons
            remaining_vol = float((qty * self.item_vol).sum())
            res = []
            for ct in ctypes:
                best = None
                for t in range(tries):
                    r = self.pack_carton(ct, qty, rng, noise=0.0 if t == 0 else 0.3)
                    if best is None or r.packed_vol > best.packed_vol:
                        best = r
                    if best.packed_vol >= remaining_vol:
                        break
                if best.packed_vol > 0:
                    res.append(best)
            if not res:
                return None
            chosen = self._pick_type(res, remaining_vol)
            cartons.append(chosen)
            for b in chosen.blocks:
                qty[b.sku] -= b.count
        return cartons

    # ------------------------------------------------------------------ local search
    @staticmethod
    def _key(cartons, min_fill):
        n = len(cartons)
        low = sum(1 for c in cartons if c.fill < min_fill)
        vol = sum(c.ctype.vol for c in cartons)
        return (n, low, vol)

    def _qty_of(self, cartons):
        q = np.zeros(self.n, dtype=np.int64)
        for c in cartons:
            for b in c.blocks:
                q[b.sku] += b.count
        return q

    def improve(self, cartons, rng, min_fill=0.75, rounds=40, tries=6, deadline=None):
        cartons = list(cartons)
        best_key = self._key(cartons, min_fill)
        for _ in range(rounds):
            if deadline and time.time() > deadline:
                break
            if len(cartons) < 2:
                break
            order = sorted(range(len(cartons)), key=lambda i: cartons[i].fill)
            improved = False
            # try subsets formed by the weakest carton plus 1-3 partners
            weakest = order[0]
            partner_pool = [i for i in order[1:]]
            attempts = []
            for k in (1, 2, 3):
                if k <= len(partner_pool):
                    attempts.append([weakest] + partner_pool[:k])
                    # also random partners
                    attempts.append([weakest] + rng.sample(partner_pool, k))
            for sub in attempts:
                if deadline and time.time() > deadline:
                    break
                sub = sorted(set(sub))
                sub_c = [cartons[i] for i in sub]
                pool = self._qty_of(sub_c)
                old_key = self._key(sub_c, min_fill)
                new_c = self.solve_pool(pool, rng, tries=tries, max_cartons=len(sub_c))
                if not new_c:
                    continue
                if self._key(new_c, min_fill) < old_key:
                    cartons = [c for i, c in enumerate(cartons) if i not in sub] + new_c
                    improved = True
                    break
            if not improved:
                # try just the weakest carton alone in smaller types
                i = weakest
                pool = self._qty_of([cartons[i]])
                new_c = self.solve_pool(pool, rng, tries=tries + 6, max_cartons=1)
                if new_c and new_c[0].ctype.vol < cartons[i].ctype.vol:
                    cartons[i] = new_c[0]
                    improved = True
            if not improved:
                break
        # final down-size pass for every carton
        for i, c in enumerate(cartons):
            pool = self._qty_of([c])
            new_c = self.solve_pool(pool, rng, tries=tries + 4, max_cartons=1)
            if new_c and new_c[0].ctype.vol < c.ctype.vol:
                cartons[i] = new_c[0]
        return cartons

    def solve(self, seed=0, tries=4, min_fill=0.75, deadline=None):
        rng = random.Random(seed)
        c = self.solve_pool(self.qty0, rng, tries=tries)
        if c is None:
            return None
        return self.improve(c, rng, min_fill=min_fill, tries=tries + 2, deadline=deadline)


# ------------------------------------------------------------------------------------
def _worker(args):
    packer, seed, tries, min_fill, deadline = args
    sol = packer.solve(seed=seed, tries=tries, min_fill=min_fill, deadline=deadline)
    return seed, sol


def optimize(skus, ctypes, mode="uniform", upright_only=False, time_budget=20.0,
             workers=None, min_fill=0.75, base_seed=1, progress=None):
    """Multi-start optimisation. Returns (packer, best_cartons)."""
    packer = Packer(skus, ctypes, mode=mode, upright_only=upright_only)
    if packer.qty0.sum() == 0:
        return packer, []
    # Cap workers to prevent OOM in container environments (e.g. Railway 512MB RAM limit)
    max_workers_limit = int(os.environ.get("MAX_WORKERS", "2"))
    workers = workers or min(max_workers_limit, max(1, (os.cpu_count() or 2)))
    start = time.time()
    best, best_key = None, None
    seed = base_seed
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    tries = 4
    wave = 0
    try:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                while True:
                    wave += 1
                    deadline = start + time_budget
                    jobs = [(packer, seed + i, tries, min_fill, deadline) for i in range(workers)]
                    seed += workers
                    for _, sol in ex.map(_worker, jobs):
                        if sol is None:
                            continue
                        k = packer._key(sol, min_fill)
                        if best is None or k < best_key:
                            best, best_key = sol, k
                    if progress:
                        progress(min(1.0, (time.time() - start) / time_budget), best_key)
                    if time.time() - start > time_budget * 0.6 or wave >= 6:
                        break
        else:
            raise RuntimeError("single worker")
    except Exception:
        while time.time() - start < time_budget and wave < 6:
            wave += 1
            sol = packer.solve(seed=seed, tries=tries, min_fill=min_fill, deadline=start + time_budget)
            seed += 1
            if sol is None:
                continue
            k = packer._key(sol, min_fill)
            if best is None or k < best_key:
                best, best_key = sol, k
    return packer, best or []


# ------------------------------------------------------------------------------------
def best_grid_count(sku: Sku, ctype: CType, upright_only=False):
    """Best simple single-orientation grid count (used to validate/derive full-carton capacity)."""
    upright = upright_only or getattr(sku, "top_up", False)
    X, Y, Z = ctype.dims_s
    best = (0, None)
    for pid, p in enumerate(PERMS_ALL):
        if upright and p[2] != 2:
            continue
        d = sku.dims_s
        a, b, c = d[p[0]], d[p[1]], d[p[2]]
        cnt = (X // a) * (Y // b) * (Z // c)
        if cnt > best[0]:
            best = (cnt, pid)
    return best


def full_carton_layout(sku: Sku, ctype: CType, count: int, mode="per_product", upright_only=False, seed=7):
    """Arrangement of `count` boxes of ONE product in a carton. Returns (Carton, achieved_count)."""
    upright = upright_only or getattr(sku, "top_up", False)
    one = Sku(0, sku.key, sku.code, sku.name, sku.l, sku.w, sku.h, count, sku.units_per_inner, top_up=upright)
    pk = Packer([one], [ctype], mode=mode, upright_only=upright)
    rng = random.Random(seed)
    best = None
    # pure grid first (cleanest instructions)
    X, Y, Z = ctype.dims_s
    best_pid = None
    best_cnt = 0
    exact_pid = None
    for pid_cand, p in enumerate(PERMS_ALL):
        if upright and p[2] != 2:
            continue
        d = one.dims_s
        a, b, c = d[p[0]], d[p[1]], d[p[2]]
        c_grid = (X // a) * (Y // b) * (Z // c)
        if c_grid == count:
            exact_pid = pid_cand
            best_pid = pid_cand
            best_cnt = c_grid
            break
        if c_grid > best_cnt:
            best_cnt = c_grid
            best_pid = pid_cand

    pid = exact_pid if exact_pid is not None else best_pid
    cnt = best_cnt

    if cnt >= count and pid is not None:
        p = PERMS_ALL[pid]
        d = one.dims_s
        a, b, c = d[p[0]], d[p[1]], d[p[2]]
        mx, my, mz = X // a, Y // b, Z // c
        blocks = []
        remaining = count
        per_layer = mx * my
        full_layers = remaining // per_layer
        if full_layers:
            blocks.append(Block(0, pid, 0, 0, 0, mx, my, full_layers, a, b, c))
            remaining -= full_layers * per_layer
        z0 = full_layers * c
        if remaining:
            rows = remaining // mx
            if rows:
                blocks.append(Block(0, pid, 0, 0, z0, mx, rows, 1, a, b, c))
                remaining -= rows * mx
            if remaining:
                blocks.append(Block(0, pid, 0, rows * b, z0, remaining, 1, 1, a, b, c))
        pv = count * int(pk.item_vol[0])
        return Carton(ctype, blocks, pv), count
    for t in range(60):
        r = pk.pack_carton(ctype, np.array([count]), rng, noise=0.0 if t == 0 else 0.35)
        if best is None or r.packed_vol > best.packed_vol:
            best = r
        if best.packed_vol >= count * pk.item_vol[0]:
            break
    got = sum(b.count for b in best.blocks)
    return best, got


# ------------------------------------------------------------------------------------
def expand_boxes(carton: Carton):
    """List of (sku, perm, x, y, z, dx, dy, dz) for each individual box."""
    out = []
    for b in carton.blocks:
        for (x, y, z) in b.boxes():
            out.append((b.sku, b.perm, x, y, z, b.dx, b.dy, b.dz))
    return out


def validate_carton(carton: Carton, skus):
    """Independent geometric validation. Returns list of problems (empty == valid)."""
    problems = []
    X, Y, Z = carton.ctype.dims_s
    boxes = expand_boxes(carton)
    if not boxes:
        return problems
    arr = np.array([(b[2], b[3], b[4], b[5], b[6], b[7]) for b in boxes], dtype=np.int64)
    # bounds
    if (arr[:, 0] < 0).any() or (arr[:, 1] < 0).any() or (arr[:, 2] < 0).any():
        problems.append("negative coordinate")
    if ((arr[:, 0] + arr[:, 3]) > X).any() or ((arr[:, 1] + arr[:, 4]) > Y).any() or ((arr[:, 2] + arr[:, 5]) > Z).any():
        problems.append("box outside carton")
    # dimensions match orientation
    for b in boxes:
        d = skus[b[0]].dims_s
        p = PERMS_ALL[b[1]]
        if (d[p[0]], d[p[1]], d[p[2]]) != (b[5], b[6], b[7]):
            problems.append("orientation/dims mismatch")
            break
        if getattr(skus[b[0]], "top_up", False) and p[2] != 2:
            problems.append(f"box {skus[b[0]].name} is not positioned with top side facing upward")
            break
    # overlap (pairwise, vectorised)
    n = len(arr)
    x0, y0, z0 = arr[:, 0], arr[:, 1], arr[:, 2]
    x1, y1, z1 = x0 + arr[:, 3], y0 + arr[:, 4], z0 + arr[:, 5]
    ox = (x0[:, None] < x1[None, :]) & (x0[None, :] < x1[:, None])
    oy = (y0[:, None] < y1[None, :]) & (y0[None, :] < y1[:, None])
    oz = (z0[:, None] < z1[None, :]) & (z0[None, :] < z1[:, None])
    ov = ox & oy & oz
    np.fill_diagonal(ov, False)
    if ov.any():
        problems.append("overlapping boxes: %d pairs" % (ov.sum() // 2))
    # support: every box with z>0 must be fully supported by boxes whose top == its bottom (area check)
    for i in range(n):
        if z0[i] == 0:
            continue
        under = (z1 == z0[i]) & ox[i] & oy[i]
        under[i] = False
        area = 0
        for j in np.where(under)[0]:
            wx = min(x1[i], x1[j]) - max(x0[i], x0[j])
            wy = min(y1[i], y1[j]) - max(y0[i], y0[j])
            area += wx * wy
        if area < (x1[i] - x0[i]) * (y1[i] - y0[i]) * 0.999:
            problems.append("unsupported box")
            break
    return problems
