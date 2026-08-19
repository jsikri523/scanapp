/* ============================================================
   scanapp: reporting views

   This is where counting happens. The table keeps every scan; these
   views decide what counts. Keeping the two apart means the counting
   rule can change without rescanning anything and without losing the
   record of what happened on the floor.
   ============================================================ */

/* ------------------------------------------------------------
   WHAT THE BARCODE CONTAINS

   Four backtick delimited numeric segments:

       3225 ` 237 ` 8156 ` 0
       schedule ` unit number ` master key ` parent key

   Meanings confirmed by Sahab, 19 August 2026. Which of segments 3
   and 4 is master and which is parent is still being confirmed by him;
   the scans support the order above (see below).

   Observed across 80 real scans, 17 and 19 August 2026:

   1. The master and parent keys form a hierarchy. In schedule 3236:

          master 2456  parent 0        20 scans
          master 8156  parent 0        18 scans
          master 8385  parent 2456      8 scans

      8385 sits under 2456. Segment 4 was zero on every scan except
      those and one mullion label, 2966`81`8746`8492, which is the kind
      of combination unit that would have a parent.

   2. THE UNIT NUMBER IS NOT UNIQUE ON ITS OWN. In schedule 3236, unit
      315 and unit 321 each appear under two different master keys:

          3236`315`2456`0      3236`315`8385`2456
          3236`321`2456`0      3236`321`8385`2456

      Over the same 46 scans: 33 distinct payloads, 33 distinct
      (master, unit) pairs, but only 31 distinct unit numbers. Counting
      unit numbers alone loses records. Everything below therefore keys
      on the whole payload.

   3. Master keys are reused across schedules. 8156 appears in both
      schedule 3225 and schedule 3236.

   4. Some labels carry a marketing QR returning
      https://www.vinylbilt.com/. It is not delimited, so it parses as
      unreadable and shows in vw_ScanEvent_Unreadable rather than
      counting.

   STILL OPEN, and it governs what these views mean:
   does the unit number identify the WINDOW or the individual CUT PIECE?
   If it identifies the window, several pieces share a payload and the
   counted view below is counting windows, not pieces. Nothing here
   assumes an answer.
   ------------------------------------------------------------ */


/* ------------------------------------------------------------
   vw_ScanEvent_Counted
   The FIRST scan of each distinct payload at each station.

   Deduplication is on Station plus the whole RawScanValue, never on
   UnitNo alone, for the reason in note 2 above.

   What one row means depends on the open question. If the unit number
   is a piece, a row is a piece. If it is a window, a row is a window.
   Read it as "one distinct label identity, scanned at least once".
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Counted
AS
WITH ranked AS
(
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.Station, s.RawScanValue
            ORDER BY s.ScanTimestamp, s.ScanID
        ) AS rn
    FROM dbo.ScanEvent AS s
    WHERE s.ParseOK = 1          -- marketing QR and misreads do not count
)
SELECT
    ScanID, ScanTimestamp, Station, Operator,
    ScheduleNo, UnitNo, MasterKey, ParentKey,
    OrderNo, BatchNo, BinNo,
    PartCode, PartPosition, PartTotal,
    UnitID, ProductCode,
    SawFile, ScheduleVersion,
    RawScanValue, ParseOK
FROM ranked
WHERE rn = 1;
GO


/* ------------------------------------------------------------
   vw_ScanEvent_Repeats
   Everything the counting view set aside: a scanner double read, the
   operator scanning a second code on the same label, or a genuine
   rescan later.

   Worth watching during the pilot. A high repeat rate is a signal, not
   a fault: it may mean a label carries several codes with the same
   payload, or that pieces of one window share a unit number. Both are
   things we want to learn rather than hide.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Repeats
AS
WITH ranked AS
(
    SELECT
        s.ScanID, s.ScanTimestamp, s.Station, s.Operator,
        s.ScheduleNo, s.UnitNo, s.MasterKey, s.RawScanValue,
        ROW_NUMBER() OVER (
            PARTITION BY s.Station, s.RawScanValue
            ORDER BY s.ScanTimestamp, s.ScanID
        ) AS rn
    FROM dbo.ScanEvent AS s
    WHERE s.ParseOK = 1
)
SELECT ScanID, ScanTimestamp, Station, Operator,
       ScheduleNo, UnitNo, MasterKey, RawScanValue,
       rn AS ScanNumber
FROM ranked
WHERE rn > 1;
GO


/* ------------------------------------------------------------
   vw_ScanEvent_Unreadable
   Scans that were saved but could not be parsed. Should be near zero
   apart from the marketing QR. If it is not, the parsing in
   scan_parser.py is wrong and needs correcting against RawScanValue.
   Nothing is lost either way, because the raw payload is always kept.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Unreadable
AS
SELECT ScanID, ScanTimestamp, Station, Operator, RawScanValue
FROM dbo.ScanEvent
WHERE ParseOK = 0;
GO


/* ------------------------------------------------------------
   vw_ScanEvent_MasterTree
   The master and parent structure as the scans see it. Useful for
   confirming which segment is master and which is parent, and for
   showing what a parent groups together.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_MasterTree
AS
SELECT
    ScheduleNo,
    MasterKey,
    ParentKey,
    COUNT(*)                    AS Scans,
    COUNT(DISTINCT UnitNo)      AS DistinctUnits,
    MIN(ScanTimestamp)          AS FirstSeen,
    MAX(ScanTimestamp)          AS LastSeen
FROM dbo.ScanEvent
WHERE ParseOK = 1 AND MasterKey IS NOT NULL
GROUP BY ScheduleNo, MasterKey, ParentKey;
GO


/* ============================================================
   THE SCHEDULE SIDE

   The operator screen needs to know, for a station: which saw file is
   being cut, how many labels are in it, how many have been scanned,
   and what is queued next.

   The scan half comes from vw_ScanEvent_Counted above.

   The schedule half does NOT need the database. Established 19 August
   2026 by comparing a printed run label against the machine's own
   schedule folder:

       run label:  SCHEDULE 3225 / 208 Labels / 4 Parts / JMC SAW 5
       SAW 5 copy of schedule 3225: 208 csv rows, 4 part prefixes
                                    (03, 03F, 05, 05F)

   One label per csv row. Each machine holds its own portion of a
   schedule, refreshed daily, and the row count is the label count. So
   the denominator can be read straight off the machine folder.

   Two things to note before relying on it:
     - a schedule is split across machines, and the same schedule has
       different content and different row counts on each
     - it is not yet confirmed that labels at a given station belong to
       that station. Labels scanned on 19 August came from schedules
       held by SAW 3, JSAW and SAW 2, not SAW 5

   Nothing is built against this yet, because whether a row is a piece
   or a window is the same open question as above.
   ============================================================ */
