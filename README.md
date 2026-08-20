# scanapp

Scan capture for the SAW 5 pilot. A Flask application built to the same shape as
the other AppX apps on `vb-wappx01-of`, so it deploys beside `labelapp`,
`locationapp`, `signapp` and `logoapp` rather than introducing anything new.

The operator screen follows the mock-up shown to Anthony and Daniel on
17 August 2026. Daniel said it "looks good"; no formal sign-off was recorded, so
treat the layout as agreed in principle rather than approved.

> **Status: not deployed.** Nothing in this repository has run on the AppX
> server. Every deployment instruction below is written from a read-only audit
> of `vb-wappx01-of` taken on 20 August 2026 and is unverified until it is
> actually executed.

## The four stages, and what each one proves

These are separate deployments with separate risks. Do not run them together.

| Stage | What it proves | Database | Gunicorn workers | Gate |
|---|---|---|---|---|
| **1. Platform-path test** | The app installs, starts, is reachable through the router, and the tablet can open it. One or two throwaway scans | none, demo mode | **1** | Pre-deployment checklist complete |
| **2. Controlled demo capture test** | A known set of labels produces a known set of rows in `scans_captured.jsonl`. This is the test that answers what the QR codes contain | none, demo mode | **1** | Stage 1 passed, capture file confirmed writing |
| **3. Real SAW 5 capture session** | A production shift of scanning at the station | none, demo mode | **1** | Stage 2 passed, and a decision that demo-mode file capture is acceptable as the record |
| **4. Production database deployment** | Scans land in SQL Server | `SCANAPP_DB_DSN` set | 1, until shared state is verified | Sahab's table and view exist, and the read path has been tested against them |

## Run it locally, with no database

```
cd scanapp
.venv/Scripts/python.exe wsgi.py
```

Then open <http://localhost:5005/station/saw5>.

With no `SCANAPP_DB_DSN` set the app runs in demo mode. The full scan flow works
and every scan is appended to `scans_captured.jsonl`.

To simulate a scan without a scanner, click the page and type a payload then
press Enter. A real captured payload:

```
3225`237`8156`0
```

## Gunicorn workers: exactly one in demo mode

**Demo mode requires `--workers 1`. This is not a preference.**

Demo state (`_DEMO` in `app/db.py`) lives in module globals guarded by a
`threading.Lock`. Gunicorn forks worker processes, so each worker holds its own
independent copy. With more than one worker:

- the count on the operator screen jumps between refreshes depending on which
  worker answers the poll
- duplicate detection misses roughly half of all repeats, because
  `scan_already_counted()` only sees scans handled by the same worker
- `/api/recent`, the endpoint that exists to answer what the QR codes encode,
  returns a partial list

`deploy/scanapp.service` ships with `--workers 1` and a comment saying why.

**Raise it above 1 only after ScanApp holds its state in a verified shared
database**, not before. At that point the in-memory demo path is no longer in
use and the worker count stops mattering.

## Durable capture in demo mode

In demo mode `scans_captured.jsonl` in the application directory is the **only**
durable record. Everything else is in process memory and dies with the worker.

Because of that, a failed write is treated as a failed scan:

- the failure is logged at ERROR level, visible in `journalctl -u scanapp`
- `POST /api/scan` returns 503
- the tablet queues the scan and retries; the operator sees "queued", never a
  successful capture
- neither the scan count nor the duplicate set is advanced

**The file must exist and grow after the first controlled demo scan.** Check it:

```bash
ls -la /var/www/apps/scanapp/scans_captured.jsonl
sudo tail -2 /var/www/apps/scanapp/scans_captured.jsonl
```

**If that file is not created, or stops growing, stop scanning immediately.**
The most likely cause is ownership: the application directory must be writable
by `www-data`. See the ownership step in the deployment section.

## How the pieces fit

```
wsgi.py                 Gunicorn entry point
config.py               environment driven settings, station list
app/routes.py           the operator screen
app/api.py              POST /api/scan, GET /api/status, POST /api/issue
app/db.py               everything that touches SQL, plus demo mode
app/scan_parser.py      raw scanner payload to fields
app/templates/          station.html is the operator screen
app/static/             scanapp.css, scanapp.js
sql/001_create_scan_table.sql
sql/002_views.sql       counting, deduping, and the schedule view for Sahab
deploy/scanapp.service  systemd unit
deploy/nginx-scanapp.conf
```

`sql/` and `ingest_capture.py` are shipped for reference. Neither is executed by
the running application.

## The two rules the design rests on

**Every scan is written. Nothing is ever rejected.**

We do not yet know which of the QR codes on a label the operator will scan, or
what each one encodes. So the raw payload is stored verbatim on every row,
parsing is best effort, and a scan that cannot be read is still saved.

That means scanning can start before the label format is fully understood. When
it is confirmed, re-parsing history is one UPDATE against `RawScanValue`.

**The table records, the view counts.**

`ScanEvent` keeps every scan. The reporting view decides which ones count.

## What the barcode contains

The QR codes do not carry the printed label text. A real scan returns a compact
backtick delimited record, established by scanning real labels on 17 and
19 August 2026:

```
3225`237`8156`0
```

| Segment | Name in the parser | Basis | Confidence |
|---|---|---|---|
| 1 | `schedule_no` | Matches the printed `SCH:` on every label checked | **Confirmed** |
| 2 | `unit_no` | Sahab, 19 August 2026 | Relayed verbally, **not seen in writing** |
| 3 | `master_key` | Sahab, 19 August 2026 | Relayed verbally, **not seen in writing** |
| 4 | `parent_key` | Sahab, 19 August 2026 | Relayed verbally, **not seen in writing** |

Segment 4 was zero on every scan except one mullion label, `2966`81`8746`8492`,
and a mullion is a combination unit, which is the one case that would be
expected to have a parent.

**An earlier reading of segment 2 as a cut list line number was wrong**, and an
earlier reading of it as a product type code was also wrong. Both came from
small samples and neither was checked against the database. Segment 2 has now
been read several different ways, which is the reason nothing downstream of it
should be treated as settled.

**The open question that governs the design:** does the unit number identify the
window, or the individual cut piece? A window is made of several pieces and each
piece gets its own label. If every piece of a window carries the same unit
number, collapsing repeats would undercount by roughly half. On schedule 3225 at
SAW 5 that is 208 rows against 83 distinct units.

Until that is answered, `piece_identified` is forced to `False`, the whole
payload is the only deduplication key, and counting decisions live in the
reporting view where they can be changed without rescanning anything.

**The unit number is not unique on its own.** In schedule 3236, unit 315 and
unit 321 each appear under two different master keys.

**A label carries the same code more than once**, so a long profile can be
scanned from whichever end is nearer and the piece counts once.

**Some labels carry a marketing QR** returning `https://www.vinylbilt.com/`. It
is not delimited, so it is stored, flagged unreadable, and never counted.

## Scanner input

The app does not depend on the scanner sending Enter. During testing a scanner
produced line breaks in Notepad but its terminator never reached Chrome as an
Enter keydown, so the buffer filled and nothing sent. A wedge scanner types far
faster than a person, so the app submits on the pause after the burst, 120ms of
quiet. Enter and Tab still submit immediately when they arrive.

Verified on the tablet at SAW 5 on 19 August 2026.

## Deploying to AppX

Confirmed about `vb-wappx01-of` from the audit on 20 August 2026: Python 3.12.3,
`ODBC Driver 18 for SQL Server` installed, nginx 1.24.0 listening on **8080
only**, `/run/gunicorn` created by `/etc/tmpfiles.d/gunicorn.conf` and shared by
all apps, apps owned `www-data:www-data`.

> Run the pre-deployment checklist and capture a baseline first. The checklist,
> the stage verdicts and the package manifest are in
> `FINAL_PREDEPLOYMENT_VERIFICATION_2026-08-20.md`, which lives in the
> **project folder alongside this repository, not inside the deployment
> package**. Read it before running anything below.

```bash
# 1. install the files
sudo mkdir -p /var/www/apps/scanapp
sudo python3 -m zipfile -e /tmp/scanapp_deploy_20260820_v2.zip /var/www/apps/scanapp/

# 2. OWNERSHIP. Not optional.
#    The service runs as www-data and must be able to CREATE
#    scans_captured.jsonl in this directory. Without this the capture file is
#    never written and, in demo mode, every scan is lost.
sudo chown -R www-data:www-data /var/www/apps/scanapp
ls -ld /var/www/apps/scanapp        # must show www-data www-data

# 3. virtual environment
cd /var/www/apps/scanapp
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
sudo chown -R www-data:www-data /var/www/apps/scanapp/venv

# 4. prove it as the service user, before any service exists
sudo -u www-data venv/bin/python -c "from app import create_app; c=create_app().test_client(); r=c.get('/station/saw5'); print(r.status_code, len(r.data))"

# 5. systemd
sudo cp deploy/scanapp.service /etc/systemd/system/
grep -c RuntimeDirectory /etc/systemd/system/scanapp.service   # active directive must be absent
grep -- --workers /etc/systemd/system/scanapp.service          # must be 1
sudo systemctl daemon-reload
sudo systemctl enable --now scanapp
ls -la /run/gunicorn/               # scanapp.sock added, the other four still present

# 6. nginx. Edit sites-available, NOT the sites-enabled symlink: an editor that
#    replaces rather than writes in place would turn the symlink into a regular
#    file and desynchronise the two directories.
sudo cp /etc/nginx/sites-available/appx-router /etc/nginx/sites-available/appx-router.bak-$(date +%Y%m%d-%H%M)
sudo nano /etc/nginx/sites-available/appx-router     # add the block from deploy/nginx-scanapp.conf
diff /etc/nginx/sites-available/appx-router.bak-<exact-name> /etc/nginx/sites-available/appx-router
sudo nginx -t
sudo systemctl reload nginx
```

The `diff` matters more than `nginx -t`. A syntax error is caught by `nginx -t`;
an edit that is valid but touches a neighbouring `location` block is not. **The
diff must show only added lines.**

### The tablet URL

**Currently the URL to test is:**

```
http://10.50.0.101:8080/scanapp/station/saw5
```

**No trailing slash after `saw5`.** With a trailing slash Flask returns 404 for
this route shape, and it does not redirect.

`http://.../scanapp` without a trailing slash also returns 404, because it does
not match `location /scanapp/` and the router has no catch-all. Only
`/scanapp/...` works. The other four apps behave the same way.

> **Unverified.** This URL has never been requested. It is derived from the
> audit, not from a successful load. It is confirmed only when a browser opens
> it, and the tablet path is confirmed only when the tablet on plant Wi-Fi opens
> it.

```
https://appx.vinylbilt.com/scanapp/station/saw5
```

> **Unverified, and currently not expected to work.** `vb-wappx01-of` has no
> listener on 443 or 80, no certbot and no letsencrypt directory. Something
> else terminates TLS for that hostname and proxies to 8080: a valid Let's
> Encrypt certificate for `appx.vinylbilt.com` was observed from the server,
> expiring 30 October 2026, and nothing on this box renews it. Which host does
> that, and whether the tablet subnet can reach it, is an open question for
> Sahab. Do not put this URL on the tablet until it is answered.

## Rollback

Run these checks **before** deploying. Without them you cannot tell whether you
broke something or it was already broken.

```bash
ls -la /run/gunicorn/ | tee /tmp/baseline_sockets.txt
systemctl is-active labelapp locationapp signapp logoapp | tee /tmp/baseline_services.txt
for a in labelapp locationapp signapp logoapp; do printf '%-14s ' $a; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/$a/; done | tee /tmp/baseline_routes.txt
```

Also required before touching nginx:

- the `appx-router` backup exists, and **its exact filename is written down**
- the `diff` against that backup has been reviewed and shows only additions
- all four existing applications are verified again after the reload

### Rollback triggers

Roll back if any of these occur:

- any original AppX route stops returning its baseline status
- any original Gunicorn socket disappears from `/run/gunicorn/`
- any original service becomes inactive
- `nginx -t` fails after the edit
- ScanApp produces repeated 500 or 502 responses
- ScanApp cannot create `scans_captured.jsonl`
- ScanApp reports successful scans without durable persistence
- the nginx diff contains any change outside the intended ScanApp block

### Rollback procedure

```bash
# 1. Stop and disable ScanApp only. Nothing else is touched.
sudo systemctl stop scanapp
sudo systemctl disable scanapp
sudo rm -f /etc/systemd/system/scanapp.service
sudo systemctl daemon-reload
sudo rm -f /run/gunicorn/scanapp.sock

# 2. Restore the exact nginx backup taken during deployment.
sudo cp /etc/nginx/sites-available/appx-router.bak-<exact-name> /etc/nginx/sites-available/appx-router

# 3. Validate before applying. Do not reload on a failed test.
sudo nginx -t

# 4. Reload only if step 3 succeeded.
sudo systemctl reload nginx     # if nginx is down: sudo systemctl restart nginx

# 5. Check the original four against the baseline.
diff /tmp/baseline_sockets.txt <(ls -la /run/gunicorn/)
systemctl is-active labelapp locationapp signapp logoapp
for a in labelapp locationapp signapp logoapp; do printf '%-14s ' $a; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/$a/; done
```

**Step 6, conditional and only if needed.** Restart an existing application
**only** if step 5 shows it is unhealthy or its socket is missing. Restarting a
healthy production app is itself an outage, so it is not an automatic step.

```bash
# Example, for a confirmed-unhealthy app only:
sudo systemctl restart labelapp
```

## Open items this code is waiting on

| Ref | What is needed | Who | Where it bites |
|---|---|---|---|
| Q1 | Does the unit number identify the window or the cut piece | Sahab | Every counting decision. The largest open item |
| Q2 | Segments 2, 3 and 4 confirmed in writing | Sahab | `scan_parser.py` field names |
| Q3 | An account that can insert into the scan table, **not `sa`** | Sahab | `SCANAPP_DB_DSN` in the unit file |
| Q4 | What each of the QR codes on a label encodes | Daniel | `scan_parser.py` patterns |
| Q9 | Where the production schedule is read from | Sahab | `db.get_station_status()` |
| Q15 | What makes one complete window | Daniel | The screen counts pieces, not windows, and says so |
| Q16 | Which host terminates TLS for `appx.vinylbilt.com`, and can the tablet reach it | Sahab | The tablet URL |
| Q17 | Whether Addison already built scan capture | Jatin | Duplication. Nothing was found on the AppX server |

## Deliberate omissions

**Window completion.** The screen shows position in schedule as printed on the
label, not a window complete state. The earlier assumption of two labels per
window, height and width, is not supported by the labels photographed at SAW 5.
Rather than build a rule on an assumption that looks wrong, the screen reports
what it can prove.

**Remake suffixes.** Agreed verbally on 10 August and still unowned. Order
numbers on the label already carry a suffix (`42305-1.2`), so confirm with
Daniel whether that is the same mechanism before building a second one.

**Supervisor notification.** `POST /api/issue` records the event. Who gets told,
and how, is not defined anywhere yet. The endpoint now returns 503 rather than a
false success when the record cannot be written, but the operator screen does
not yet surface that distinction.

**Authentication.** There is none. Any device on the plant network can post
scans, and stray keystrokes near the tablet become rows flagged unreadable. This
is a known gap, not an oversight.

## The SQL path is untested

`pyodbc` is not installed in the local development virtual environment, so
`import pyodbc` has been taking the `ImportError` branch throughout development.
**No part of the SQL path has ever executed on any machine.** Treat stage 4 as
unproven code, not lightly tested code.
