"""
JSON endpoints the operator screen talks to.

Two rules run through all of this:

  1. A scan is never lost. If parsing fails, if the piece is not on the
     current schedule, if we cannot tell whether it is a duplicate, the row
     is still written. The screen tells the operator what happened, the
     database keeps the evidence.

  2. The response tells the screen which state to show. The screen holds no
     judgement of its own about what a scan means.
"""

from flask import Blueprint, jsonify, request

from config import config
from . import db, scan_parser

bp = Blueprint("api", __name__)


@bp.post("/scan")
def scan():
    """
    Record one scan.

    Expects JSON: {"station": "saw5", "raw": "<payload>",
                   "client_scan_id": "<uuid>", "operator": "<optional>"}

    Returns: {"state": ok|dup|unexpected|error, ...} for the screen to render.
    """
    body = request.get_json(silent=True) or {}
    raw = body.get("raw")
    station_key = body.get("station")
    client_scan_id = body.get("client_scan_id")
    operator = body.get("operator")

    st = config.STATIONS.get(station_key)
    if not st:
        return jsonify({"state": "error", "message": "Unknown station."}), 400

    if not raw or not str(raw).strip():
        return jsonify({"state": "error", "message": "Scan the label again."}), 400

    rec = scan_parser.parse(raw, station_code=st["code"])

    # Anthony's plan asks for Machine as well as Station. The machine name is
    # the one printed on the run label, 'JMC SAW 5', so it matches what an
    # operator or supervisor sees on paper.
    rec["machine"] = st.get("fenevision_id")

    # Work out what to tell the operator BEFORE writing, so the check sees
    # the state of the world without this scan in it.
    #
    # An identical payload means the same UNIT, not necessarily the same
    # piece. Until Sahab confirms whether the unit number identifies the
    # window or the piece, a repeat is reported to the operator as seen
    # before and nothing more. It is still written and still counted. The
    # decision about what counts belongs in the reporting view, where it can
    # be changed without rescanning anything.
    already = db.scan_already_counted(st["code"], rec.get("raw_scan_value"))

    try:
        db.insert_scan(rec, operator=operator, client_scan_id=client_scan_id)
    except Exception as exc:
        # The tablet will queue this and retry. Do not pretend it worked.
        return jsonify({
            "state": "error",
            "message": "Scan not saved. It will be sent again automatically.",
            "detail": str(exc),
        }), 503

    status = db.get_station_status(st["code"])

    if already:
        state = "dup"
    elif not rec["parse_ok"]:
        # Stored, but we could not read the label. Worth the operator knowing.
        state = "unexpected"
    else:
        state = "ok"

    return jsonify({
        "state": state,
        "scan": {
            "order_no": rec["order_no"],
            "unit_id": rec["unit_id"],
            "unit_no": rec["unit_no"],
            "master_key": rec["master_key"],
            "parent_key": rec["parent_key"],
            "schedule_no": rec["schedule_no"],
            "part_code": rec["part_code"],
            "product_code": rec["product_code"],
            "bin_no": rec["bin_no"],
            "position": rec["part_position"],
            "total": rec["part_total"],
            "segments": rec["segments"],
        },
        "status": status,
    })


@bp.get("/status/<station_key>")
def status(station_key):
    """Counts and schedule queue. Polled by the screen so it stays current."""
    st = config.STATIONS.get(station_key)
    if not st:
        return jsonify({"error": "Unknown station."}), 404
    return jsonify(db.get_station_status(st["code"]))


@bp.get("/recent")
def recent():
    """
    The last few scans, raw payload included.

    Demo mode only. This exists to answer open item Q4: what the QR codes on
    the SAW 5 label actually encode. Scan each code on a label, read what came
    back here, and the parser can be corrected to match.

    Not available once a database is connected. Use the table then.
    """
    if not config.demo_mode:
        return jsonify({"error": "Demo mode only. Query the ScanEvent table."}), 404

    limit = request.args.get("limit", default=25, type=int)
    rows = db._DEMO["scans"][-limit:]
    return jsonify([
        {
            "at": r["scanned_at"].strftime("%H:%M:%S"),
            "raw": r["raw_scan_value"],
            "length": len(r["raw_scan_value"] or ""),
            "parsed_ok": r["parse_ok"],
            "fields": {k: v for k, v in r.items()
                       if k not in ("raw_scan_value", "scanned_at", "operator",
                                    "parse_ok", "station") and v is not None},
        }
        for r in reversed(rows)
    ])


@bp.post("/issue")
def issue():
    """
    Operator pressed Report issue.

    SAHAB / JATIN: currently records the event only. Who gets told, and how,
    is not defined yet. It came out of the 17 August meeting as
    "notify supervisors if scan issues arise" and is not in the SRD.
    """
    body = request.get_json(silent=True) or {}
    station_key = body.get("station")
    st = config.STATIONS.get(station_key)
    if not st:
        return jsonify({"ok": False}), 400

    rec = scan_parser.parse(body.get("raw") or "", station_code=st["code"])
    rec["raw_scan_value"] = "ISSUE REPORTED: " + (body.get("raw") or "")
    try:
        db.insert_scan(rec, operator=body.get("operator"))
    except Exception:
        pass
    return jsonify({"ok": True})
