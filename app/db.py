"""
Data layer.

Everything that touches SQL lives here so Sahab has one file to work in.
Each function that needs a decision from him is marked SAHAB with what is
needed and why.

In demo mode (no SCANAPP_DB_DSN set) these functions run against in-memory
sample data so the operator screen can be demonstrated before the table exists.
"""

import datetime
import json
import logging
import os
import threading

from config import config

log = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
    """Raised when a scan could not be written durably."""


def _utcnow():
    """Timezone-aware UTC. datetime.utcnow() is deprecated from Python 3.12."""
    return datetime.datetime.now(datetime.timezone.utc)


def _naive_utc(dt):
    """
    Drop the offset for consumers that expect the previous naive UTC shape.

    The JSONL capture file and the SQL ScanTimestamp column have carried naive
    UTC since the start ('2026-08-19T19:27:56'). Keeping that shape means this
    change stays internal and nothing already written has to be re-read.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)

try:
    import pyodbc
except ImportError:  # demo mode on a machine without the driver
    pyodbc = None


# ----------------------------------------------------------------------
# Connection
# ----------------------------------------------------------------------

def _connect():
    if not config.DB_DSN:
        raise RuntimeError("SCANAPP_DB_DSN is not set")
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed")
    return pyodbc.connect(config.DB_DSN, timeout=config.DB_TIMEOUT)


def healthcheck():
    """Used by /scanapp/healthz so we can see the DB is reachable."""
    if config.demo_mode:
        return {"ok": True, "mode": "demo"}
    try:
        with _connect() as cn:
            cn.cursor().execute("SELECT 1").fetchone()
        return {"ok": True, "mode": "sql"}
    except Exception as exc:
        log.error("Healthcheck failed against SQL. error=%s", exc)
        return {"ok": False, "mode": "sql", "error": "database unavailable"}


# ----------------------------------------------------------------------
# Writing scans
# ----------------------------------------------------------------------

INSERT_SQL = """
INSERT INTO [{schema}].[{table}]
    (ScanTimestamp, Station, Machine, Operator,
     ScheduleNo, UnitNo, MasterKey, ParentKey,
     OrderNo, BatchNo, BinNo,
     PartCode, PartPosition, PartTotal,
     UnitID, ProductCode,
     SawFile, ScheduleVersion,
     RawScanValue, ParseOK, ClientScanID, CreatedAt)
VALUES
    (?, ?, ?, ?,
     ?, ?, ?, ?,
     ?, ?, ?,
     ?, ?, ?,
     ?, ?,
     ?, ?,
     ?, ?, ?, SYSUTCDATETIME());
"""


def insert_scan(rec, operator=None, client_scan_id=None, scanned_at=None):
    """
    Write one scan.

    Deliberately does NOT reject duplicates. Every scan the operator makes is
    recorded, including rescans. Counting is done in the deduped view, so a
    rescan never inflates a count but is still visible when someone asks what
    actually happened on the floor.

    ClientScanID is a UUID generated on the tablet. It exists so the offline
    queue can retry safely: replaying the same scan twice is harmless because
    the reporting view ignores the second copy.
    """
    scanned_at = scanned_at or _utcnow()

    if config.demo_mode:
        return _demo_insert(rec, operator, client_scan_id, scanned_at)

    sql = INSERT_SQL.format(schema=config.SCAN_SCHEMA, table=config.SCAN_TABLE)
    with _connect() as cn:
        cn.cursor().execute(
            sql,
            _naive_utc(scanned_at), rec.get("station"), rec.get("machine"), operator,
            rec.get("schedule_no"), rec.get("unit_no"),
            rec.get("master_key"), rec.get("parent_key"),
            rec.get("order_no"), rec.get("batch_no"), rec.get("bin_no"),
            rec.get("part_code"), rec.get("part_position"), rec.get("part_total"),
            rec.get("unit_id"), rec.get("product_code"),
            rec.get("saw_file"), rec.get("schedule_version"),
            rec.get("raw_scan_value"), 1 if rec.get("parse_ok") else 0,
            client_scan_id,
        )
        cn.commit()
    return {"stored": True}


def scan_already_counted(station, raw_value):
    """
    Has this exact payload been seen before at this station?

    Matching is on the WHOLE payload, never on UnitNo alone. Observed on the
    floor on 19 August 2026: in schedule 3236, unit 315 and unit 321 each
    appear under two different master keys, so the unit number is unique only
    within its master.

    Note what this does and does not tell you. It says the same payload came
    back. It does NOT say the same physical piece came back, because whether
    the unit number identifies a window or a single cut piece is still open
    with Sahab. If it identifies a window, two pieces of that window produce
    this same payload legitimately.

    So this is used only to label what the operator screen shows. It never
    blocks the insert and never suppresses a count. If we cannot tell, we say
    no and let the scan through: a missing scan is worse than an extra row
    that a view can drop later.
    """
    if not raw_value:
        return False

    if config.demo_mode:
        return raw_value in _DEMO["seen"]

    sql = """
        SELECT TOP 1 1
        FROM [{schema}].[{table}]
        WHERE Station = ? AND RawScanValue = ?
    """.format(schema=config.SCAN_SCHEMA, table=config.SCAN_TABLE)
    try:
        with _connect() as cn:
            return cn.cursor().execute(sql, station, raw_value).fetchone() is not None
    except Exception as exc:
        # Cannot tell. Say no and let the scan through: a missing scan is worse
        # than an extra row a view can drop later. Still logged, because a
        # persistently failing duplicate check is a real fault.
        log.warning("Duplicate check failed, treating scan as new. station=%s error=%s",
                    station, exc)
        return False


# ----------------------------------------------------------------------
# Reading progress
# ----------------------------------------------------------------------

def get_station_status(station_code):
    """
    Everything the operator screen needs in one call.

    SAHAB: this is the read side and it is the part I need from you.
    It has to return the current schedule at the station, how many pieces
    it contains, how many have been scanned, and the schedules queued after it.

    Two things are still open and both affect this function:

      Q9  Where does the production schedule come from? The app has no
          schedule of its own. It needs a source for "what is SAW 5 cutting
          today, and how many pieces are in each schedule".

      Q15 What counts as one complete window? The label carries two unit IDs
          (502257 and 502258 on the same label), so a scanned piece is not
          necessarily a finished window. Until that rule is defined this
          returns piece counts, not window counts, and the screen says
          "pieces" so nobody reads it as windows.

    Suggested shape once the source is known: a view or stored procedure
    that takes a station and returns one row per schedule with
    (SawFile, ScheduleNo, PieceTotal, PieceScanned, Sequence, State).
    Then this function is a single SELECT and the app never holds
    schedule logic of its own.
    """
    if config.demo_mode:
        return _demo_status(station_code)

    sql = """
        -- SAHAB: replace with the real view or stored procedure.
        SELECT SawFile, ScheduleNo, PieceTotal, PieceScanned, Sequence, State
        FROM [{schema}].[{view}]
        WHERE Station = ?
        ORDER BY Sequence
    """.format(schema=config.SCAN_SCHEMA, view=config.SCAN_VIEW)

    # A missing view, an unreachable server, a renamed schema or a changed
    # column must not take the operator screen down. An operator staring at a
    # blank tablet cannot scan, and losing scans is worse than losing counts.
    try:
        with _connect() as cn:
            rows = cn.cursor().execute(sql, station_code).fetchall()
    except Exception as exc:
        log.error(
            "Station status query failed, serving unavailable state. station=%s schema=%s view=%s error=%s",
            station_code, config.SCAN_SCHEMA, config.SCAN_VIEW, exc,
        )
        return _unavailable_status("Database status unavailable.")

    try:
        schedules = [
            {
                "file": r.SawFile,
                "schedule_no": r.ScheduleNo,
                "total": r.PieceTotal,
                "scanned": r.PieceScanned,
                "state": r.State,
            }
            for r in rows
        ]
    except Exception as exc:
        # The view exists but does not have the columns this code expects.
        log.error(
            "Station status view returned unexpected columns. station=%s view=%s error=%s",
            station_code, config.SCAN_VIEW, exc,
        )
        return _unavailable_status("Database status unavailable.")

    return _shape_status(schedules)


def _shape_status(schedules, status_available=True, status_message=None):
    """
    Common shaping so demo and SQL paths return identical structures.

    status_available says whether the figures in this payload can be trusted.
    It is True in demo mode, where the capture file is the source, and False
    only when the SQL read failed and no counts could be produced.
    """
    current = next((s for s in schedules if s["state"] == "cur"), None)
    done_count = sum(1 for s in schedules if s["state"] == "done")
    return {
        "schedules": schedules,
        "current": current,
        "done_count": done_count,
        "total_schedules": len(schedules),
        "status_available": status_available,
        "status_message": status_message,
    }


def _unavailable_status(message):
    """
    Safe fallback so the station page keeps working when SQL is not usable.

    Reports no counts rather than wrong ones, and says why. Scanning continues:
    scan capture does not depend on this read path.
    """
    return _shape_status([], status_available=False, status_message=message)


# ----------------------------------------------------------------------
# Demo mode
# ----------------------------------------------------------------------
# Sample data taken from the SAW 5 cut files for batch 3194, the same set
# used in the approved mock-up. Lets the screen be shown and the scan flow
# demonstrated with no database at all.

_DEMO_LOCK = threading.Lock()

_DEMO = {
    "seen": set(),
    "scans": [],
    "schedules": [
        {"file": "03_0109", "schedule_no": "3194", "total": 6, "scanned": 6, "state": "done"},
        {"file": "03_0113", "schedule_no": "3194", "total": 6, "scanned": 6, "state": "done"},
        {"file": "03_0101", "schedule_no": "3194", "total": 106, "scanned": 88, "state": "cur"},
        {"file": "03_0114", "schedule_no": "3194", "total": 14, "scanned": 0, "state": "next"},
        {"file": "05_0101", "schedule_no": "3194", "total": 52, "scanned": 0, "state": "next"},
        {"file": "05F_0101", "schedule_no": "3194", "total": 14, "scanned": 0, "state": "next"},
        {"file": "03_1414", "schedule_no": "3194", "total": 4, "scanned": 0, "state": "next"},
        {"file": "05_0114", "schedule_no": "3194", "total": 8, "scanned": 0, "state": "next"},
        {"file": "05_0109", "schedule_no": "3194", "total": 2, "scanned": 0, "state": "next"},
        {"file": "05_1414", "schedule_no": "3194", "total": 2, "scanned": 0, "state": "next"},
    ],
}


# In demo mode there is no database, so a capture session would be lost on
# restart. Append every scan to a file as well, so a batch of labels scanned
# for analysis survives and can be handed to Sahab as evidence.
_DEMO_CAPTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scans_captured.jsonl",
)


def _demo_persist(rec, operator, scanned_at):
    """
    Append one scan to the capture file, or raise.

    In demo mode this file is the ONLY durable record. Everything else lives
    in process memory and dies with the worker. A failure here is therefore a
    lost scan and must never be swallowed: it is logged at ERROR and raised,
    the API turns it into a 503, and the tablet queues the scan and retries.

    On the server this runs as www-data, so /var/www/apps/scanapp must be
    owned by www-data. See the ownership step in the README.
    """
    row = {k: v for k, v in rec.items()}
    row["operator"] = operator
    row["scanned_at"] = _naive_utc(scanned_at).isoformat(timespec="seconds")
    try:
        with open(_DEMO_CAPTURE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        log.error(
            "DEMO CAPTURE WRITE FAILED, scan not durably stored. path=%s error=%s. Check that %s is writable by the service user (www-data).",
            _DEMO_CAPTURE, exc, os.path.dirname(_DEMO_CAPTURE),
        )
        raise PersistenceError("demo capture file could not be written") from exc


def _demo_insert(rec, operator, client_scan_id, scanned_at):
    with _DEMO_LOCK:
        # Durable write FIRST. If it raises, nothing below runs, so neither the
        # scan count nor the duplicate set is advanced for a scan that was not
        # actually stored. The caller turns the exception into a 503 and the
        # tablet queues it, so the operator is never told it was captured.
        _demo_persist(rec, operator, scanned_at)
        _DEMO["scans"].append(dict(rec, operator=operator, scanned_at=scanned_at))
        if rec.get("raw_scan_value"):
            _DEMO["seen"].add(rec["raw_scan_value"])
    return {"stored": True, "demo": True}


def _demo_status(station_code):
    """
    Demo status reports the CAPTURE SESSION, not schedule progress.

    It used to advance the sample 3194 schedule on every scan, which put a
    moving progress bar on the screen that had nothing to do with what the
    saw was cutting, and which counted duplicates as progress. In front of an
    operator that is worse than showing nothing.

    Two numbers that are true and can be checked against the file:
    scans taken, and distinct payloads among them. The gap between them is
    the piece versus window question, visible live.

    Real schedule progress needs the day's folder off the machine
    (open item Q9), and a decision on whether a scan counts a piece or a
    window (open with Sahab). Neither is settled, so neither is displayed.
    """
    with _DEMO_LOCK:
        st = _shape_status([dict(s) for s in _DEMO["schedules"]])
        st["capture"] = {
            "scans": len(_DEMO["scans"]),
            "units": len(_DEMO["seen"]),
        }
        return st
