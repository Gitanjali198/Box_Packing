"""Post-processing: consolidate cartons by moving boxes into free gaps of fuller cartons.

This guarantees the requirement "if another inner box can still be fitted into the space that is
left, it must be fitted" - after this pass no box of any lighter carton can be inserted into the
free space of a fuller carton (checked on all slide-compacted positions, in the allowed
orientations, with full support underneath).
"""
from __future__ import annotations

import numpy as np

from .engine import PERMS_ALL, Block, Carton, expand_boxes


def boxes_array(boxes):
    if not boxes:
        return np.zeros((0, 6), dtype=np.int64)
    return np.array([(b[2], b[3], b[4], b[5], b[6], b[7]) for b in boxes], dtype=np.int64)


def find_position(arr, X, Y, Z, dx, dy, dz):
    """Lowest, backmost, leftmost supported position for a (dx,dy,dz) box, or None."""
    if dx > X or dy > Y or dz > Z:
        return None
    if len(arr):
        xs = np.unique(np.concatenate(([0], arr[:, 0] + arr[:, 3])))
        ys = np.unique(np.concatenate(([0], arr[:, 1] + arr[:, 4])))
        zs = np.unique(np.concatenate(([0], arr[:, 2] + arr[:, 5])))
    else:
        xs = ys = zs = np.array([0])
    xs = xs[xs + dx <= X]
    ys = ys[ys + dy <= Y]
    zs = zs[zs + dz <= Z]
    if len(xs) == 0 or len(ys) == 0 or len(zs) == 0:
        return None
    for z in zs:                       # lowest layer first
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        px, py = gx.ravel(), gy.ravel()
        ok = np.ones(len(px), dtype=bool)
        if len(arr):
            bx0, by0, bz0 = arr[:, 0], arr[:, 1], arr[:, 2]
            bx1, by1, bz1 = bx0 + arr[:, 3], by0 + arr[:, 4], bz0 + arr[:, 5]
            zov = (bz0 < z + dz) & (bz1 > z)
            if zov.any():
                ox = (px[:, None] < bx1[None, zov]) & (bx0[None, zov] < (px + dx)[:, None])
                oy = (py[:, None] < by1[None, zov]) & (by0[None, zov] < (py + dy)[:, None])
                ok &= ~((ox & oy).any(axis=1))
        if not ok.any():
            continue
        cand = np.where(ok)[0]
        if z > 0:
            sup = (arr[:, 2] + arr[:, 5]) == z
            if not sup.any():
                continue
            bx0, by0 = arr[sup, 0], arr[sup, 1]
            bx1, by1 = bx0 + arr[sup, 3], by0 + arr[sup, 4]
            good = []
            for ci in cand:
                wx = np.minimum(px[ci] + dx, bx1) - np.maximum(px[ci], bx0)
                wy = np.minimum(py[ci] + dy, by1) - np.maximum(py[ci], by0)
                area = (np.clip(wx, 0, None) * np.clip(wy, 0, None)).sum()
                if area >= dx * dy * 0.999:
                    good.append(ci)
            cand = np.array(good, dtype=np.int64)
            if len(cand) == 0:
                continue
        # choose back-left-most
        best = min(cand, key=lambda i: (py[i], px[i]))
        return int(px[best]), int(py[best]), int(z)
    return None


def compress_boxes(boxes):
    """Merge individual boxes (sku, perm, x,y,z,dx,dy,dz) into maximal grid Blocks."""
    from collections import defaultdict
    groups = defaultdict(list)
    for sku, perm, x, y, z, dx, dy, dz in boxes:
        groups[(sku, perm, dx, dy, dz)].append((x, y, z))
    blocks = []
    for (sku, perm, dx, dy, dz), pts in groups.items():
        remaining = set(pts)
        while remaining:
            # seed = lowest z, y, x
            x0, y0, z0 = min(remaining, key=lambda p: (p[2], p[1], p[0]))
            nx = 1
            while (x0 + nx * dx, y0, z0) in remaining:
                nx += 1
            ny = 1
            while all((x0 + i * dx, y0 + ny * dy, z0) in remaining for i in range(nx)):
                ny += 1
            nz = 1
            while all((x0 + i * dx, y0 + j * dy, z0 + nz * dz) in remaining for i in range(nx) for j in range(ny)):
                nz += 1
            for k in range(nz):
                for j in range(ny):
                    for i in range(nx):
                        remaining.discard((x0 + i * dx, y0 + j * dy, z0 + k * dz))
            blocks.append(Block(sku, perm, x0, y0, z0, nx, ny, nz, dx, dy, dz))
    blocks.sort(key=lambda b: (b.z, b.y, b.x))
    return blocks


def _top_free(boxes_arr_):
    """indices of boxes that have nothing resting on them."""
    n = len(boxes_arr_)
    if n == 0:
        return []
    a = boxes_arr_
    x1, y1, z1 = a[:, 0] + a[:, 3], a[:, 1] + a[:, 4], a[:, 2] + a[:, 5]
    free = []
    for i in range(n):
        above = (a[:, 2] == z1[i]) & (a[:, 0] < x1[i]) & (a[:, 0] + a[:, 3] > a[i, 0]) & \
                (a[:, 1] < y1[i]) & (a[:, 1] + a[:, 4] > a[i, 1])
        if not above.any():
            free.append(i)
    return free


def gap_fill(cartons, skus, mode, upright_only, item_vol, deadline=None):
    """Move boxes from lighter cartons into gaps of fuller cartons. Returns new carton list."""
    import time
    state = []
    for c in cartons:
        state.append({"ctype": c.ctype, "boxes": expand_boxes(c)})
    fails = set()
    changed = True
    guard = 0
    while changed and guard < 20:
        guard += 1
        changed = False
        if deadline and time.time() > deadline:
            break
        fill = lambda s: sum(item_vol[b[0]] for b in s["boxes"]) / s["ctype"].vol
        order = sorted(range(len(state)), key=lambda i: fill(state[i]))
        for di in order:
            donor = state[di]
            if not donor["boxes"]:
                continue
            progress = True
            while progress and donor["boxes"]:
                progress = False
                arr_d = boxes_array(donor["boxes"])
                for bi in sorted(_top_free(arr_d), key=lambda i: -item_vol[donor["boxes"][i][0]]):
                    sku = donor["boxes"][bi][0]
                    targets = sorted([ti for ti in range(len(state)) if ti != di and
                                      fill(state[ti]) >= fill(donor) - 1e-9 and state[ti]["boxes"]],
                                     key=lambda t: -fill(state[t]))
                    moved = False
                    for ti in targets:
                        if (id(state[ti]), sku) in fails:
                            continue
                        tgt = state[ti]
                        X, Y, Z = tgt["ctype"].dims_s
                        arr_t = boxes_array(tgt["boxes"])
                        # allowed orientations
                        if mode == "uniform":
                            perms = {tgt["boxes"][0][1]}
                        else:
                            perms = {b[1] for b in tgt["boxes"] if b[0] == sku}
                            if not perms:
                                perms = {i for i, p in enumerate(PERMS_ALL) if (not upright_only or p[2] == 2)}
                        if upright_only or getattr(skus[sku], "top_up", False):
                            perms = {p for p in perms if PERMS_ALL[p][2] == 2}
                        d = skus[sku].dims_s
                        placed = None
                        for pid in sorted(perms):
                            p = PERMS_ALL[pid]
                            dx, dy, dz = d[p[0]], d[p[1]], d[p[2]]
                            pos = find_position(arr_t, X, Y, Z, dx, dy, dz)
                            if pos:
                                placed = (sku, pid, pos[0], pos[1], pos[2], dx, dy, dz)
                                break
                        if placed:
                            tgt["boxes"].append(placed)
                            donor["boxes"].pop(bi)
                            fails = {f for f in fails if f[0] != id(donor)}
                            moved = True
                            changed = progress = True
                            break
                        else:
                            fails.add((id(tgt), sku))
                    if moved:
                        break
    out = []
    for s in state:
        if not s["boxes"]:
            continue
        blocks = compress_boxes(s["boxes"])
        pv = int(sum(item_vol[b[0]] for b in s["boxes"]))
        out.append(Carton(s["ctype"], blocks, pv))
    return out
