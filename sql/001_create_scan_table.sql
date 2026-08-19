/* ============================================================
   scanapp: scan capture table
   SAW 5 scanning pilot, Vinylbilt traceability programme

   For review by Sahab before anything is created.

   Two design decisions worth stating up front, because they are the
   ones most likely to be argued with:

   1. ONE table for all stations, with a Station column.
      Not one table per machine. The pilot is one station but the
      programme scales to roughly fifteen tablets. A single schema
      means one change rather than fifteen, and "which saw completed
      which order" is a WHERE clause rather than a UNION.

   2. NO unique constraint on the scan.
      Every scan is written, including rescans. Counting happens in a
      view instead, so the rule can change without rescanning anything
      and the record of what actually happened on the floor is kept.

      This matters because one question is still open: does the unit
      number identify the window, or the individual cut piece? If it is
      the window, several pieces share a unit number and anything that
      collapsed them at insert time would undercount. Writing every row
      and deciding later costs nothing and cannot lose data.

   3. NO unique constraint on UnitNo either.
      Observed on the floor, 19 August 2026: in schedule 3236, unit 315
      and unit 321 each appear under two different master keys. The unit
      number is unique only within its master key.
   ============================================================ */

IF OBJECT_ID('dbo.ScanEvent', 'U') IS NOT NULL
    PRINT 'dbo.ScanEvent already exists, nothing created.';
ELSE
BEGIN

CREATE TABLE dbo.ScanEvent
(
    ScanID              BIGINT IDENTITY(1,1) NOT NULL,

    /* When and where -------------------------------------------------- */
    ScanTimestamp       DATETIME2(0)   NOT NULL,   -- UTC, set by the app
    Station             VARCHAR(32)    NOT NULL,   -- 'SAW 5'
    Machine             VARCHAR(32)    NULL,       -- 'JMC SAW 5' as printed on the run label
    Operator            VARCHAR(64)    NULL,       -- required by Anthony's plan, not yet captured
    Status              VARCHAR(32)    NULL,       -- required by Anthony's plan. FeneVision
                                                   -- uses Accepted / Complete / Rejected

    /* Processing duration is on Anthony's list and is NOT a column we can
       simply fill in. A scan records when a piece was labelled, not when it
       started. Deriving a duration needs either a second scan or a defined
       start reference, so it is left out rather than approximated. */

    /* From the barcode ------------------------------------------------- */
    /* The QR code returns four backtick delimited numeric segments:

           3225 ` 237 ` 8156 ` 0
           schedule ` unit ` master key ` parent key

       Meanings confirmed by Sahab on 19 August 2026. All nullable, because
       a scan that cannot be parsed is still stored rather than rejected. */
    ScheduleNo          VARCHAR(16)    NULL,       -- 3225
    UnitNo              VARCHAR(32)    NULL,       -- 237. FeneVision's Unit, which
                                                   -- is a WINDOW, not a cut piece.
                                                   -- Schedule 3192 holds 383 units.
    MasterKey           VARCHAR(32)    NULL,       -- 8156, from FeneVision
    ParentKey           VARCHAR(32)    NULL,       -- 0, or the combination unit this
                                                   -- one belongs to. FeneVision shows
                                                   -- unit 523 (Combo Platinum) as the
                                                   -- parent of units 61, 62, 275,
                                                   -- 521 and 522.

    /* PartNo is on Anthony's plan alongside Window Number, because the point
       of scanning at a saw is piece level rather than window level, which is
       what FeneVision already does. The barcode does not appear to carry it.
       Nullable until we know whether it can be derived. */
    PartNo              VARCHAR(32)    NULL,

    /* From printed label text ------------------------------------------ */
    /* The QR codes do not carry these. They are here for the fallback path
       and for any code that turns out to encode readable text. */
    OrderNo             VARCHAR(32)    NULL,       -- 42305-1.2
    BatchNo             VARCHAR(16)    NULL,       -- 4
    BinNo               VARCHAR(16)    NULL,       -- 55
    PartCode            VARCHAR(16)    NULL,       -- 5003
    PartPosition        INT            NULL,       -- 148  of 228
    PartTotal           INT            NULL,       -- 228
    UnitID              VARCHAR(32)    NULL,       -- 502258, printed, not in the barcode
    ProductCode         VARCHAR(32)    NULL,       -- 4000-DP
    SawFile             VARCHAR(64)    NULL,       -- 03F_0101.csv
    ScheduleVersion     VARCHAR(32)    NULL,       -- v260813.01

    /* Evidence --------------------------------------------------------- */
    RawScanValue        NVARCHAR(512)  NOT NULL,   -- exactly what the scanner sent
    ParseOK             BIT            NOT NULL CONSTRAINT DF_ScanEvent_ParseOK DEFAULT (0),
    ClientScanID        VARCHAR(64)    NULL,       -- uuid from the tablet, for safe retries
    CreatedAt           DATETIME2(0)   NOT NULL CONSTRAINT DF_ScanEvent_CreatedAt DEFAULT (SYSUTCDATETIME()),

    CONSTRAINT PK_ScanEvent PRIMARY KEY CLUSTERED (ScanID)
);

/* The queries the pilot actually runs. */
CREATE INDEX IX_ScanEvent_Station_Time  ON dbo.ScanEvent (Station, ScanTimestamp);
CREATE INDEX IX_ScanEvent_Schedule      ON dbo.ScanEvent (ScheduleNo, MasterKey, UnitNo);
CREATE INDEX IX_ScanEvent_Master        ON dbo.ScanEvent (MasterKey) WHERE MasterKey IS NOT NULL;
CREATE INDEX IX_ScanEvent_Order         ON dbo.ScanEvent (OrderNo) WHERE OrderNo IS NOT NULL;

/* Deduplication is on the whole payload, never on UnitNo alone. See design
   note 3 above: the same unit number occurs under more than one master. */
CREATE INDEX IX_ScanEvent_Raw           ON dbo.ScanEvent (Station, RawScanValue);

/* Retry safety. The tablet queues scans when Wi-Fi drops and resends them.
   A resend of something that did land is ignored rather than duplicated. */
CREATE UNIQUE INDEX UX_ScanEvent_ClientScanID
    ON dbo.ScanEvent (ClientScanID) WHERE ClientScanID IS NOT NULL;

PRINT 'dbo.ScanEvent created.';

END
GO
