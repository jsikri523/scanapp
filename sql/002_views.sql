/* ============================================================
   scanapp: reporting views

   This is where counting happens. The table keeps every scan; these
   views decide what counts. Keeping the two apart is what lets us
   fix the double count without losing the audit trail.
   ============================================================ */

/* ------------------------------------------------------------
   WHAT THE BARCODE CONTAINS
   ------------------------------------------------------------
   Established by scanning real labels on 17 August 2026.

   The QR payload is backtick delimited:

       schedule ` line number ` cut list ` unused

   Evidence. Eight labels from schedule 3229, both codes scanned on each:

       3229`202`8429`0   3229`103`8536`0
       3229`203`8429`0   3229`104`8536`0
       3229`204`8429`0   3229`105`8536`0
       3229`205`8429`0   3229`106`8536`0

   Eight distinct payloads, each seen exactly twice, once per duplicate
   code on its label. Segment 2 runs consecutively within each cut list.
   Consecutive integers appearing once per physical label is a line
   number, not a product code.

   Consistent with two labels from schedule 3225 scanned separately,
   3225`227`8156`0 (order 42305-1.2) and 3225`237`8156`0 (order 42518-6):
   same cut list 8156, different line numbers.

   So schedule + cut list + line number identifies the piece, and the
   whole payload is a valid deduplication key.

   NOTE: a label carries the SAME code twice, once at each end of the
   -W- FRAME -W- band, so a long profile can be scanned from whichever
   end is nearer. Both produce the same payload, so deduplication counts
   the piece once. That is intended, not a defect.

   NOTE: some labels also carry a marketing QR that returns
   https://www.vinylbilt.com/. It is not delimited, so it parses as
   unreadable and shows in vw_ScanEvent_Unreadable rather than counting.
   ------------------------------------------------------------ */


/* ------------------------------------------------------------
   vw_ScanEvent_Counted
   One row per piece per station: the FIRST time it was scanned.

   This is the fix for the FeneVision defect where a window rescanned on
   a later day is counted twice. Repeats stay in the base table and are
   visible in vw_ScanEvent_Repeats below.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Counted
AS
WITH ranked AS
(
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.Station, COALESCE(s.UnitID, s.RawScanValue)
            ORDER BY s.ScanTimestamp, s.ScanID
        ) AS rn
    FROM dbo.ScanEvent AS s
    WHERE s.ParseOK = 1          -- marketing QR and misreads do not count
)
SELECT
    ScanID, ScanTimestamp, Station, Operator,
    OrderNo, ScheduleNo, BatchNo, BinNo,
    PartCode, PartPosition, PartTotal,
    UnitID, ODKey, ProductCode,
    SawFile, ScheduleVersion,
    RawScanValue, ParseOK
FROM ranked
WHERE rn = 1;
GO


/* ------------------------------------------------------------
   vw_ScanEvent_Repeats
   Everything the counting view dropped: the second code on a label, a
   scanner double read, or a real rescan. Expect roughly one repeat per
   piece, because operators scan whichever end is nearer and sometimes
   both. A piece appearing four or five times is worth looking at.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Repeats
AS
WITH ranked AS
(
    SELECT
        s.ScanID, s.ScanTimestamp, s.Station, s.Operator,
        s.OrderNo, s.UnitID, s.RawScanValue,
        ROW_NUMBER() OVER (
            PARTITION BY s.Station, COALESCE(s.UnitID, s.RawScanValue)
            ORDER BY s.ScanTimestamp, s.ScanID
        ) AS rn
    FROM dbo.ScanEvent AS s
    WHERE s.ParseOK = 1
)
SELECT ScanID, ScanTimestamp, Station, Operator, OrderNo, UnitID, RawScanValue, rn AS ScanNumber
FROM ranked
WHERE rn > 1;
GO


/* ------------------------------------------------------------
   vw_ScanEvent_Unreadable
   Scans that were saved but could not be parsed. Should be near zero.
   If it is not, the parsing assumptions in scan_parser.py are wrong and
   need correcting against RawScanValue. Nothing is lost either way.
   ------------------------------------------------------------ */
CREATE OR ALTER VIEW dbo.vw_ScanEvent_Unreadable
AS
SELECT ScanID, ScanTimestamp, Station, Operator, RawScanValue
FROM dbo.ScanEvent
WHERE ParseOK = 0;
GO


/* ============================================================
   SAHAB: the piece below is the one I need your help with.

   The operator screen needs to know, for a station:
     - which saw file is being cut now
     - how many pieces are in it
     - how many have been scanned
     - what is queued after it

   The scan side of that (PieceScanned) comes from the view above.
   The schedule side (which files, in what order, how many pieces each)
   has to come from wherever the production schedule lives. That source
   is open item Q9 and I do not know the answer yet.

   Below is the shape the application expects. Once you point the
   schedule half at the right source, the app needs no further change:
   db.get_station_status() is a single SELECT against this.
   ============================================================ */

/*
CREATE OR ALTER VIEW dbo.vw_StationSchedule
AS
SELECT
    sch.Station,
    sch.SawFile,
    sch.ScheduleNo,
    sch.PieceTotal,
    COALESCE(scanned.PieceScanned, 0)  AS PieceScanned,
    sch.Sequence,
    CASE
        WHEN COALESCE(scanned.PieceScanned, 0) >= sch.PieceTotal THEN 'done'
        WHEN sch.Sequence = (
                SELECT MIN(s2.Sequence)
                FROM <schedule source> AS s2
                WHERE s2.Station = sch.Station
                  AND COALESCE(
                        (SELECT COUNT(*) FROM dbo.vw_ScanEvent_Counted d2
                         WHERE d2.Station = s2.Station AND d2.SawFile = s2.SawFile), 0
                      ) < s2.PieceTotal
             ) THEN 'cur'
        ELSE 'next'
    END AS State
FROM <schedule source> AS sch
LEFT JOIN (
    SELECT Station, SawFile, COUNT(*) AS PieceScanned
    FROM dbo.vw_ScanEvent_Counted
    GROUP BY Station, SawFile
) AS scanned
    ON scanned.Station = sch.Station
   AND scanned.SawFile = sch.SawFile;
GO
*/


/* ------------------------------------------------------------
   Remakes.

   Agreed verbally on 10 August and still unowned: mark remakes with a
   dash one, dash two suffix so a recut piece does not read as the
   original. Order numbers already carry a suffix on the label
   (42305-1.2), so confirm with Daniel whether that is the same thing
   before building a second mechanism for it.

   Left unimplemented deliberately rather than guessed at.
   ------------------------------------------------------------ */
