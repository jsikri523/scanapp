"""
Turns a raw scanner payload into fields.

READ THIS BEFORE CHANGING ANYTHING HERE
---------------------------------------
We do not yet know what the QR codes on the SAW 5 label actually encode.
There are three codes on a single part label: two either side of the
-W- FRAME -W- band at the top, and one at the bottom beside the window
diagram. Which one the operator scans, and what each returns, is still
open with Daniel.

So this module is deliberately built to fail safe:

  * The raw payload is ALWAYS preserved verbatim on the scan row.
  * Parsing is best effort. Anything it cannot read comes back as None.
  * A scan is never rejected because parsing failed.

That means we can start capturing production scans before the label
format is fully understood, and correct the parsing later without
having lost anything. Re-parsing historic rows from RawScanValue is a
single UPDATE once the format is confirmed.

The patterns below are derived from what is PRINTED on the label
photographed at SAW 5 on 17 August 2026. The printed text is not
necessarily what the barcode contains. Treat every pattern here as a
hypothesis until a real scan is captured.
"""

import re

# Printed on the part label, in the order they appear:
#
#   GREENFOX WINS AND DOORS        dealer, Vinylbilt manufactures for them
#   42305-1.2 | 4000-DP            order number | product code
#   SCH: 3225 BAT: 4 BIN: 55       schedule, batch, bin
#   31 11/16 W X 18 5/16 H         finished size
#   [5003] 148/228                 profile code, position in schedule
#   1 of 1   v260813.01   502258   sheet, schedule version, unit id
#   A21                            rack position
#   260813-1515                    optimisation run stamp
#   502257                         second unit id, lower QR
#
# Run label (printed once per run, not per part):
#   SCHEDULE 3225 / 208 Labels / 4 Parts / JMC SAW 5 / 03F_0101.csv
#   OPT: 2026-08-13 15:15:34

PATTERNS = {
    # 42305-1.2 or 42518-6
    "order_no": re.compile(r"\b(\d{5}-\d+(?:\.\d+)?)\b"),
    # SCH: 3225  or  SCHEDULE 3225
    "schedule_no": re.compile(r"\bSCH(?:EDULE)?:?\s*(\d{3,6})\b", re.I),
    "batch_no": re.compile(r"\bBAT:?\s*(\d+)\b", re.I),
    "bin_no": re.compile(r"\bBIN:?\s*(\d+)\b", re.I),
    # [5003] 148/228
    "part_code": re.compile(r"\[(\d{3,5})\]"),
    "position": re.compile(r"\b(\d{1,5})\s*/\s*(\d{1,5})\b"),
    # v260813.01
    "schedule_version": re.compile(r"\b(v\d{6}\.\d{2})\b", re.I),
    # six digit unit identifier, 502258 / 503823
    "unit_id": re.compile(r"\b(5\d{5})\b"),
    # 03F_0101.csv
    "saw_file": re.compile(r"\b(\d{2}F?_\d{4}\.csv)\b", re.I),
    # 4000-DP, 400-PP, 5003F
    "product_code": re.compile(r"\b(\d{3,4}-[A-Z]{2})\b"),
}


# ----------------------------------------------------------------------
# The real format, captured from the scanner on 17 August 2026
# ----------------------------------------------------------------------
# The QR codes do NOT contain the printed label text. A real scan returns a
# compact backtick delimited record:
#
#     2966`81`8746`8492
#
# Four numeric fields. What each one means is NOT yet confirmed. None of them
# match the values printed on the labels photographed the same morning
# (schedule 3225, bin 55, part 5003, position 148/228, units 502257/502258),
# so the mapping has to be established by scanning labels whose printed
# values we can see, and correlating.
#
# Until that is done the segments are captured positionally and named
# nothing. Guessing here would put wrong data in the table under a
# convincing column name, which is worse than leaving it unmapped.

DELIM = "`"


def parse_delimited(text):
    """Split a delimited payload into its segments. No meaning assigned."""
    if DELIM not in text:
        return None
    parts = [p.strip() for p in text.split(DELIM)]
    return parts if len(parts) >= 2 else None


def parse(raw, station_code=None):
    """
    Parse a raw scanner payload.

    Returns a dict that is always safe to insert. Unknown fields are None.
    `raw` is echoed back untouched as raw_scan_value.
    """
    text = (raw or "").strip()

    out = {
        "raw_scan_value": raw,
        "station": station_code,
        "order_no": None,
        "schedule_no": None,
        "batch_no": None,
        "bin_no": None,
        "part_code": None,
        "part_position": None,
        "part_total": None,
        "schedule_version": None,
        "unit_id": None,
        "saw_file": None,
        "product_code": None,
        # ODKey is what FeneVision counts by. We have not yet identified
        # which field on the label corresponds to it, if any (Q4 to Daniel).
        # Until that is answered, leave it null rather than guessing, and
        # dedupe on unit_id in the reporting view.
        "odkey": None,
        # Segments of the delimited payload. Meanings from Sahab, 19 Aug 2026.
        "segments": None,
        "segment_count": None,
        "unit_no": None,
        "master_key": None,
        "parent_key": None,
        # Whether the payload distinguishes this PIECE from another piece of
        # the same window. Unresolved: see the note on segment 2 below.
        "piece_identified": False,
        "parse_ok": False,
    }

    if not text:
        return out

    # ---- the real format first -------------------------------------
    segments = parse_delimited(text)
    if segments:
        out["segments"] = segments
        out["segment_count"] = len(segments)
        out["parse_ok"] = True

        # Segment 1 is the schedule. Confirmed against two labels on
        # 17 August 2026: SCH 2966 and SCH 3225 both matched exactly.
        out["schedule_no"] = segments[0] or None

        # Segments 2 to 4, per Sahab on 19 August 2026:
        #
        #   3225 ` 237 ` 8156 ` 0
        #    |      |      |     |
        #    |      |      |     parent key
        #    |      |      master key, from a FeneVision table
        #    |      unit number
        #    schedule number
        #
        # Sahab is still confirming which of segments 3 and 4 is master and
        # which is parent. The scans support the order above: segment 4 was
        # zero on all 33 scans except one mullion label, 2966`81`8746`8492,
        # and a mullion is a combination unit, the one case that would have
        # a parent.
        #
        # An earlier reading of segment 2 as a cut list line number was
        # wrong. It was drawn from the pattern in the 17 August scans and
        # never confirmed against the database.
        out["unit_no"] = segments[1] or None
        if len(segments) > 2:
            out["master_key"] = segments[2] or None
        if len(segments) > 3:
            out["parent_key"] = segments[3] or None

        # DO NOT ASSUME THIS IDENTIFIES A PIECE.
        #
        # Segment 2 is the UNIT, which in fenestration is the window, not the
        # cut piece. A window is made of several pieces and each piece gets
        # its own label. If every piece of a window carries the same unit
        # number, then two labels from one window produce the same payload
        # and collapsing them would undercount by roughly half. On schedule
        # 3225 at SAW 5 that is 208 rows against 83 distinct units.
        #
        # This is unresolved and is the open question with Sahab: does the
        # unit number identify the window, or the individual piece?
        #
        # Until it is answered the payload is treated as a scan key only. The
        # row is always written and never suppressed, so whichever way the
        # answer falls, history can be recounted from RawScanValue without
        # anything having been lost.
        out["unit_id"] = text
        out["piece_identified"] = False

        return out

    # ---- fallback: printed-text style payloads ----------------------
    # Retained in case some codes on the label do carry readable text.

    def grab(key, group=1):
        m = PATTERNS[key].search(text)
        return m.group(group) if m else None

    out["order_no"] = grab("order_no")
    out["schedule_no"] = grab("schedule_no")
    out["batch_no"] = grab("batch_no")
    out["bin_no"] = grab("bin_no")
    out["part_code"] = grab("part_code")
    out["schedule_version"] = grab("schedule_version")
    out["unit_id"] = grab("unit_id")
    out["saw_file"] = grab("saw_file")
    out["product_code"] = grab("product_code")

    m = PATTERNS["position"].search(text)
    if m:
        out["part_position"] = int(m.group(1))
        out["part_total"] = int(m.group(2))

    # A scan counts as parsed if we got anything that identifies the piece.
    out["parse_ok"] = bool(out["unit_id"] or out["order_no"])

    return out
