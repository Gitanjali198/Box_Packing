"""Run:  python -m pytest tests   (or python tests/test_engine.py)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from packer.service import Service
from packer.planner import Settings, plan, PERMS_ALL
from packer.engine import validate_carton

def run(pi, mode):
    sv = Service()
    p = sv.parse(os.path.join(os.path.dirname(__file__), "..", "data", "sample_pis", pi))
    specs, _ = sv.build_specs([{"line": e["line"], "resolution": e["resolution"]} for e in p["lines"]])
    res = plan(specs, sv.cartons(), Settings(mode=mode, time_budget=6, allowed_cartons=sv.default_allowed()))
    return res

def test_counts_and_fill():
    for mode in ("uniform", "per_product"):
        res = run("MYERS_PI_073.xlsx", mode)
        packed = sum(c["boxes"] for c in res["mixed"])
        expected = sum(s["remainder"] for s in res["skus"])
        assert packed == expected
        assert res["summary"]["avg_mixed_fill"] > 0.8

def test_versa_top_up_orientation():
    sv = Service()
    top_items = [it for it in sv.master.items if it.top_up]
    assert len(top_items) > 0, "Top-up items must be identified in master file"

    p = sv.parse(os.path.join(os.path.dirname(__file__), "..", "data", "sample_pis", "MYERS_PI_073.xlsx"))
    specs, _ = sv.build_specs([{"line": e["line"], "resolution": e["resolution"]} for e in p["lines"]])

    for mode in ("per_product", "uniform"):
        res = plan(specs, sv.cartons(), Settings(mode=mode, time_budget=6, allowed_cartons=sv.default_allowed()))
        # Check full cartons
        for f in res["full"]:
            if f.get("top_up"):
                for b in f["layout"]["blocks"]:
                    assert PERMS_ALL[b["perm"]][2] == 2, f"Full carton {f['name']} box not top-up"
        # Check mixed cartons
        for m in res["mixed"]:
            for b in m["blocks"]:
                sku_obj = [s for s in res["skus"] if s["idx"] == b["sku"]][0]
                if sku_obj.get("top_up"):
                    assert PERMS_ALL[b["perm"]][2] == 2, f"Mixed carton {m['no']} box {sku_obj['name']} not top-up"

def test_carton_one_inch_reduction():
    sv = Service()
    cartons = sv.cartons()
    # Check that 17x17x13.5 carton has actual outer dimensions in l, w, h and reduced dimensions in calc_l, calc_w, calc_h
    assert "17x17x13.5" in cartons
    c = cartons["17x17x13.5"]
    assert c["l_in"] == 17.0 and c["w_in"] == 17.0 and c["h_in"] == 13.5
    assert c["calc_l_in"] == 16.0 and c["calc_w_in"] == 16.0 and c["calc_h_in"] == 12.5
    assert c["l"] == round(17.0 * 25.4, 1)
    assert c["w"] == round(17.0 * 25.4, 1)
    assert c["h"] == round(13.5 * 25.4, 1)
    assert c["calc_l"] == round(16.0 * 25.4, 1)
    assert c["calc_w"] == round(16.0 * 25.4, 1)
    assert c["calc_h"] == round(12.5 * 25.4, 1)

    # Check all cartons in master
    for key, spec in cartons.items():
        assert spec["calc_l_in"] == max(0.1, round(spec["l_in"] - 1.0, 2))
        assert spec["calc_w_in"] == max(0.1, round(spec["w_in"] - 1.0, 2))
        assert spec["calc_h_in"] == max(0.1, round(spec["h_in"] - 1.0, 2))

    # Verify that plan result 3D carton uses actual dimensions while calculation uses reduced dimensions
    res = run("MYERS_PI_073.xlsx", "uniform")
    for f in res["full"]:
        ct = f["carton"]
        assert ct["l"] == round(ct["outer_l_in"] * 25.4, 1)
        assert ct["w"] == round(ct["outer_w_in"] * 25.4, 1)
        assert ct["h"] == round(ct["outer_h_in"] * 25.4, 1)
        assert ct["calc_l"] == round(ct["calc_l_in"] * 25.4, 1)
        assert ct["calc_w"] == round(ct["calc_w_in"] * 25.4, 1)
        assert ct["calc_h"] == round(ct["calc_h_in"] * 25.4, 1)
    for m in res["mixed"]:
        ct = m["carton"]
        assert ct["l"] == round(ct["outer_l_in"] * 25.4, 1)
        assert ct["w"] == round(ct["outer_w_in"] * 25.4, 1)
        assert ct["h"] == round(ct["outer_h_in"] * 25.4, 1)
        assert ct["calc_l"] == round(ct["calc_l_in"] * 25.4, 1)
        assert ct["calc_w"] == round(ct["calc_w_in"] * 25.4, 1)
        assert ct["calc_h"] == round(ct["calc_h_in"] * 25.4, 1)

if __name__ == "__main__":
    test_carton_one_inch_reduction()
    test_counts_and_fill()
    test_versa_top_up_orientation()
    print("OK")
