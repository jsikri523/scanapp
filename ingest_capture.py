"""
Ingest a plain text capture file into the same shape the app produces.

For a floor session where the app is not running and the operator scans
straight into Notepad. One payload per line, exactly as the wedge scanner
types it. Blank lines and the marketing QR are handled.

    .venv/Scripts/python.exe ingest_capture.py "SAW5_2026-08-19.txt"

Writes scans_captured.jsonl in the same format as demo mode, so everything
downstream reads it without knowing where it came from.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import scan_parser

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scans_captured.jsonl")


def main(path, station="SAW 5"):
    # The scanner's terminator makes one line per trigger pull in Notepad.
    # Nothing else in the file is meaningful, so anything blank is dropped
    # and everything else is treated as a scan, readable or not.
    with open(path, encoding="utf-8-sig") as fh:
        lines = [ln.strip() for ln in fh]

    payloads = [ln for ln in lines if ln]
    stamp = datetime.datetime.now().replace(microsecond=0)

    written = 0
    seen = {}
    with open(OUT, "a", encoding="utf-8") as out:
        for i, raw in enumerate(payloads):
            rec = scan_parser.parse(raw, station_code=station)
            rec["operator"] = None
            # No per scan timestamps in a text capture. Sequence is real,
            # clock time is not, so it is marked rather than invented.
            rec["scanned_at"] = stamp.isoformat(timespec="seconds")
            rec["sequence"] = i + 1
            rec["time_is_estimated"] = True
            rec["source_file"] = os.path.basename(path)
            out.write(json.dumps(rec) + "\n")
            seen[raw] = seen.get(raw, 0) + 1
            written += 1

    repeats = {k: v for k, v in seen.items() if v > 1}
    unread = [p for p in payloads if not scan_parser.parse(p, station)["parse_ok"]]

    print(f"{written} scans read from {os.path.basename(path)}")
    print(f"{len(seen)} distinct payloads, {len(repeats)} seen more than once")
    print(f"{len(unread)} did not parse")
    if repeats:
        print("\nrepeated:")
        for k, v in sorted(repeats.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {v}x  {k}")
    if unread:
        print("\nunparsed:")
        for p in unread[:10]:
            print(f"  {p[:90]}")
    print(f"\nappended to {OUT}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: ingest_capture.py <textfile> [station]")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "SAW 5")
