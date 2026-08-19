"""
Configuration for scanapp.

Everything is read from the environment so nothing sensitive lives in the repo.
On the AppX server these go in the systemd unit (see deploy/scanapp.service).

If SCANAPP_DB_DSN is not set the app runs in demo mode: the operator screen
works end to end against in-memory sample data and nothing is written to SQL.
That is what you use to show the screen before the table exists.
"""

import os


class Config:
    SECRET_KEY = os.environ.get("SCANAPP_SECRET_KEY", "dev-only-change-me")

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    # NOTE: the AppX platform documentation records two different addresses
    # for the FeneVision SQL Server. Review documents say 10.60.0.21
    # (VBSQLFVPROD22), the running AppX apps connect to 10.0.0.21.
    # This is open item E4.4 / Q3. Confirm with Sahab before pointing
    # anything at production.
    DB_DSN = os.environ.get("SCANAPP_DB_DSN")  # full pyodbc connection string
    DB_TIMEOUT = int(os.environ.get("SCANAPP_DB_TIMEOUT", "5"))

    # Schema and table names, so Sahab can rename without touching code.
    SCAN_SCHEMA = os.environ.get("SCANAPP_SCHEMA", "dbo")
    SCAN_TABLE = os.environ.get("SCANAPP_SCAN_TABLE", "ScanEvent")
    SCAN_VIEW = os.environ.get("SCANAPP_SCAN_VIEW", "vw_ScanEvent_Counted")

    # ------------------------------------------------------------------
    # Stations
    # ------------------------------------------------------------------
    # A station is bound to the tablet by URL: /scanapp/station/saw5
    # This is one of the three options in open item Q1 (station binding).
    # It is the simplest to run for a one station pilot and needs no
    # device identifier, no login and no setup screen.
    STATIONS = {
        "saw5": {
            "code": "SAW 5",
            "name": "SAW 5 Scanning",
            "fenevision_id": "JMC SAW 5",   # as printed on the run label
        },
        # Cleaner 3 is not confirmed as part of the pilot (Q12).
        # Left here so adding a station is a config change, not a code change.
        # "cleaner3": {"code": "CLEANER 3", "name": "Cleaner 3 Scanning",
        #              "fenevision_id": "JMC CLEANER 3"},
    }

    # How often the operator screen re-reads counts, in milliseconds.
    # Open item Q6 covers what the screen shows and how often it refreshes.
    REFRESH_MS = int(os.environ.get("SCANAPP_REFRESH_MS", "15000"))

    # Window for treating an identical payload as one trigger pull read
    # twice rather than two pieces. The barcode carries no piece identifier,
    # so two genuine pieces of the same type scan identically and this is
    # the only duplicate protection available. Keep it short.
    DOUBLE_FIRE_SECONDS = int(os.environ.get("SCANAPP_DOUBLE_FIRE_SECONDS", "5"))

    @property
    def demo_mode(self):
        return not self.DB_DSN


config = Config()
