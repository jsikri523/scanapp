# scanapp

Scan capture for the SAW 5 pilot. A Flask application built to the same shape as
the other AppX apps on `vb-wappx01-of`, so it deploys beside `labelapp`,
`locationapp`, `signapp` and `logoapp` rather than introducing anything new.

The operator screen is the mock-up Anthony and Daniel approved on 17 August 2026,
wired up to real data.

## Run it now, with no database

```
cd scanapp
.venv/Scripts/python.exe wsgi.py
```

Then open <http://localhost:5005/station/saw5>.

With no `SCANAPP_DB_DSN` set the app runs in demo mode against in-memory sample
data taken from the SAW 5 cut files for batch 3194. The full scan flow works.
This is what to show Sahab in the meeting.

To simulate a scan without a scanner, click the page and type a label payload
then press Enter. This one parses cleanly:

```
GREENFOX WINS AND DOORS 42305-1.2 4000-DP SCH: 3225 BAT: 4 BIN: 55 [5003] 148/228 v260813.01 502258
```

## How the pieces fit

```
wsgi.py                 Gunicorn entry point
config.py               environment driven settings, station list
app/routes.py           the operator screen
app/api.py              POST /api/scan, GET /api/status, POST /api/issue
app/db.py               everything that touches SQL, plus demo mode
app/scan_parser.py      raw scanner payload to fields
app/templates/          station.html is the approved mock-up
app/static/             scanapp.css, scanapp.js
sql/001_create_scan_table.sql
sql/002_views.sql       counting, deduping, and the schedule view for Sahab
deploy/scanapp.service  systemd unit
deploy/nginx-scanapp.conf
```

## The two rules the design rests on

**Every scan is written. Nothing is ever rejected.**

We do not yet know what the QR codes on the label encode. There are three on a
single part label and which one the operator scans is still open with Daniel. So
the raw payload is stored verbatim on every row, parsing is best effort, and a
scan that cannot be read is still saved and still counted as having happened.

That means scanning can start before the label format is understood. When the
format is confirmed, re-parsing history is one UPDATE against `RawScanValue`.

**The table records, the view counts.**

`ScanEvent` keeps every scan. `vw_ScanEvent_Counted` decides which ones count.
`vw_ScanEvent_DoubleReads` shows what it removed.

## What the barcode actually contains

Established by scanning real labels on 17 August 2026. The payload is backtick
delimited:

```
schedule ` line number ` cut list ` unused
```

| Segment | Meaning | Basis |
|---|---|---|
| 1 | Schedule number | Matches printed `SCH:` on every label checked |
| 2 | Line number within the cut list | Runs consecutively, one per physical label |
| 3 | Cut list identifier | Two distinct values within one schedule |
| 4 | Unused so far | Zero except on a mullion label |

Twenty-five scans across three schedules. The decisive sample was eight labels
from schedule 3229 with both codes scanned on each:

```
3229`202`8429`0   3229`103`8536`0
3229`203`8429`0   3229`104`8536`0
3229`204`8429`0   3229`105`8536`0
3229`205`8429`0   3229`106`8536`0
```

Eight distinct payloads, each seen exactly twice. Consecutive integers in
segment 2, appearing once per label, is a line number rather than a product
code. An earlier reading of segment 2 as a product type came from a two-label
sample and was wrong.

**Schedule plus cut list plus line number identifies the piece**, so the whole
payload is a valid deduplication key.

**A label carries the same code twice**, once at each end of the
`-W- FRAME -W-` band, so a long profile can be scanned from whichever end is
nearer. Both give the same payload and the piece counts once. That eases the
scanner cable reach problem at the label table.

**Some labels also carry a marketing QR** returning `https://www.vinylbilt.com/`.
It is not delimited, so it is stored, flagged unreadable, and never counted.

## Scanner input

The app does not depend on the scanner sending Enter. During testing a scanner
produced line breaks in Notepad but its terminator never reached Chrome as an
Enter keydown, so the buffer filled and nothing sent. A wedge scanner types far
faster than a person, so the app submits on the pause after the burst, 120ms of
quiet. Enter and Tab still submit immediately when they arrive. This means the
app works regardless of how the scanner is configured, which matters because
the tablet may not match the test machine.

## Deploying to AppX

```
sudo mkdir -p /var/www/apps/scanapp
# copy the contents of this folder there, without .venv
cd /var/www/apps/scanapp
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

sudo cp deploy/scanapp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scanapp

# add the location block from deploy/nginx-scanapp.conf to
# /etc/nginx/sites-enabled/appx-router
sudo nginx -t && sudo systemctl reload nginx
```

The tablet then opens `https://appx.vinylbilt.com/scanapp/station/saw5` as its
kiosk browser home page. Nothing is installed on the device and nothing goes
through MDM, so updates land as soon as the app is redeployed.

This also keeps Apache Guacamole out of the scanning path entirely. Guacamole is
an undocumented dependency and its behaviour with keyboard wedge scanner input
has never been tested (open item Q16). It is still needed for FeneVision, but
not for this.

## Open items this code is waiting on

| Ref | What is needed | Who | Where it bites |
|---|---|---|---|
| Q4 | What each of the three QR codes encodes | Daniel | `scan_parser.py` patterns are hypotheses until a real scan is captured |
| Q9 | Where the production schedule is read from | Sahab | `db.get_station_status()` and `vw_StationSchedule` in `sql/002_views.sql` |
| Q15 | What makes one complete window | Daniel | The screen counts pieces, not windows, and says so |
| E4.4 | Which SQL address is correct, 10.0.0.21 or 10.60.0.21 | Sahab | The DSN in `deploy/scanapp.service` |
| Q2 | How the operator is identified | Daniel | `Operator` is nullable and currently never set |
| Q1 | How a tablet is bound to a station | Jatin | Bound by URL for now, the simplest of the three options |

## Deliberate omissions

**Window completion.** The screen shows position in schedule as printed on the
label, not a window complete state. The earlier assumption of two labels per
window, height and width, is not supported by the labels photographed at SAW 5,
which carry two unit IDs on a single label. Rather than build a rule on an
assumption that looks wrong, the screen reports what it can prove. This is the
one visible difference from the approved mock-up.

**Remake suffixes.** Agreed verbally on 10 August and still unowned. Order
numbers on the label already carry a suffix (`42305-1.2`), so confirm with Daniel
whether that is the same mechanism before building a second one.

**Supervisor notification.** `POST /api/issue` records the event. Who gets told,
and how, came out of the 17 August meeting and is not defined anywhere yet.
