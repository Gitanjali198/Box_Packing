"""Kwik Patch - Carton Packing System (web application).

Run:  python app.py       then open http://127.0.0.1:5000
"""
import os
import shutil
import sys
import time
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory

from packer.export import build_workbook
from packer.service import DATA, MASTER_PATH, UPLOADS, Service

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
import multiprocessing
_svc = None

def get_svc() -> Service:
    global _svc
    if _svc is None:
        _svc = Service()
    return _svc

class _LazyServiceProxy:
    def __getattr__(self, name):
        return getattr(get_svc(), name)

svc = _LazyServiceProxy()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "kwik_packer"}), 200


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/info")
def info():
    cs = svc.cartons()
    return jsonify({
        "cartons": list(cs.values()),
        "default_allowed": svc.default_allowed(),
        "master_items": len(svc.master.items),
        "cpu": os.cpu_count(),
    })


@app.route("/api/pi/upload", methods=["POST"])
def pi_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls"):
        return jsonify({"error": "Please upload an Excel file (.xlsx / .xls)"}), 400
    fid = uuid.uuid4().hex[:10]
    path = os.path.join(UPLOADS, fid + ext)
    f.save(path)
    try:
        sheets = svc.pi_sheets(path)
    except Exception as ex:
        return jsonify({"error": f"Cannot read file: {ex}"}), 400
    return jsonify({"file_id": fid + ext, "filename": f.filename, "sheets": sheets})


@app.route("/api/pi/parse", methods=["POST"])
def pi_parse():
    d = request.get_json(force=True)
    fid = os.path.basename(d["file_id"])
    path = os.path.join(UPLOADS, fid)
    if not os.path.exists(path):
        return jsonify({"error": "file expired, please upload again"}), 404
    try:
        res = svc.parse(path, d.get("sheet"))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 400
    return jsonify(res)


@app.route("/api/master/search")
def master_search():
    return jsonify(svc.search_master(request.args.get("q", ""), 30))


@app.route("/api/item/custom", methods=["POST"])
def item_custom():
    d = request.get_json(force=True)
    spec = d["spec"]
    svc.save_custom_item(d["line"], spec)
    return jsonify({"ok": True})


@app.route("/api/item/alias", methods=["POST"])
def item_alias():
    d = request.get_json(force=True)
    from packer.pi_parser import PILine
    ln = d["line"]
    line = PILine(idx=ln["idx"], row=ln["row"], code=ln["code"], name=ln["name"], extra=ln.get("extra", ""),
                  units_per_box=ln["units_per_box"], boxes=ln["boxes"], units=ln.get("units"))
    svc.remember_master(line, int(d["row"]))
    return jsonify({"ok": True})


@app.route("/api/cartons", methods=["POST"])
def add_carton():
    d = request.get_json(force=True)
    key = svc.add_custom_carton(d["name"], float(d["l_in"]), float(d["w_in"]), float(d["h_in"]))
    return jsonify({"key": key})


@app.route("/api/master/upload", methods=["POST"])
def master_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    tmp = os.path.join(UPLOADS, "master_new.xlsx")
    f.save(tmp)
    try:
        from packer.master import load_master
        m = load_master(tmp)
        if not m.items:
            raise ValueError("no items found - expected the 'Main data' sheet layout")
    except Exception as ex:
        return jsonify({"error": f"Not a valid master file: {ex}"}), 400
    shutil.copy(MASTER_PATH, MASTER_PATH + ".bak")
    shutil.move(tmp, MASTER_PATH)
    svc.reload_master()
    return jsonify({"ok": True, "items": len(svc.master.items)})


@app.route("/api/missing_template", methods=["POST"])
def missing_template():
    import io
    d = request.get_json(force=True)
    data = svc.missing_template(d["entries"])
    return send_file(io.BytesIO(data), as_attachment=True, download_name="missing_master_data.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/master/export")
def master_export():
    import io
    return send_file(io.BytesIO(svc.master_with_custom()), as_attachment=True,
                     download_name="master_with_my_items.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/solve", methods=["POST"])
def solve():
    d = request.get_json(force=True)
    jid = svc.start_job(d["lines"], d.get("settings", {}), float(d.get("margin_adjust", 0) or 0), d.get("meta"))
    return jsonify({"job": jid})


@app.route("/api/job/<jid>")
def job(jid):
    j = svc.jobs.get(jid)
    if not j:
        return jsonify({"error": "unknown job"}), 404
    out = {"status": j["status"], "progress": j["progress"], "error": j["error"],
           "elapsed": round(time.time() - j["started"], 1)}
    if j["status"] == "done":
        out["result"] = j["result"]
    return jsonify(out)


@app.route("/api/export/<jid>.xlsx")
def export(jid):
    j = svc.jobs.get(jid)
    if not j or j["status"] != "done":
        return jsonify({"error": "no result"}), 404
    import io
    data = build_workbook(j["result"])
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"packing_plan_{jid}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\n  Kwik Patch Carton Packing System\n  Running on http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
