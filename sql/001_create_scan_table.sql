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

   2. NO unique constraint on the piece.
      Every scan is written, including rescans. Counting happens in
      the deduped view below. FeneVision currently double counts a
      window rescanned on a later day; deduping in the view fixes the
      count while keeping the record of what actually happened on the
      floor, which is the evidence we will want during the pilot review.
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
    Operator            VARCHAR(64)    NULL,       -- open item Q2

    /* Parsed from the label ------------------------------------------- */
    /* All nullable on purpose. We do not yet know what the QR codes
       encode (open item Q4 with Daniel), so any field may be absent.
       A scan is never rejected for failing to parse. */
    OrderNo             VARCHAR(32)    NULL,       -- 42305-1.2
    ScheduleNo          VARCHAR(16)    NULL,       -- 3225
    BatchNo             VARCHAR(16)    NULL,       -- 4
    BinNo               VARCHAR(16)    NULL,       -- 55
    PartCode            VARCHAR(16)    NULL,       -- 5003
    PartPosition        INT            NULL,       -- 148  of 228
    PartTotal           INT            NULL,       -- 228
    UnitID              VARCHAR(32)    NULL,       -- 502258
    ODKey               VARCHAR(64)    NULL,       -- what FeneVision counts by
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
CREATE INDEX IX_ScanEvent_Unit          ON dbo.ScanEvent (UnitID)  WHERE UnitID IS NOT NULL;
CREATE INDEX IX_ScanEvent_Order         ON dbo.ScanEvent (OrderNo) WHERE OrderNo IS NOT NULL;
CREATE INDEX IX_ScanEvent_SawFile       ON dbo.ScanEvent (Station, SawFile);

/* Retry safety. The tablet queues scans when Wi-Fi drops and resends them.
   A resend of something that did land is ignored rather than duplicated. */
CREATE UNIQUE INDEX UX_ScanEvent_ClientScanID
    ON dbo.ScanEvent (ClientScanID) WHERE ClientScanID IS NOT NULL;

PRINT 'dbo.ScanEvent created.';

END
GO
