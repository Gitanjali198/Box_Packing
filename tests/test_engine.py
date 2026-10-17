"""Run:  python -m pytest tests   (or python tests/test_engine.py)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from packer.service import Service
from packer.planner import Settings, plan
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

if __name__ == "__main__":
    test_counts_and_fill(); print("OK")
