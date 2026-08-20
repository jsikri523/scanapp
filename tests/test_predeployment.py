"""
Pre-deployment verification suite for scanapp.

Standard library and Flask only. No pytest, no new dependencies, nothing extra
to install on the AppX server.

    python tests/test_predeployment.py

SAFETY
------
No test in this file connects to SQL Server, and none can. The database
failure paths are exercised by replacing db._connect with a function that
raises, so no driver call and no socket is ever opened. A guard test asserts
that SCANAPP_DB_DSN never contains a real server address.

Every test writes its capture file into a temporary directory, so the real
scans_captured.jsonl is never touched.
"""

import datetime
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

for _v in ("SCANAPP_DB_DSN", "SCANAPP_SECRET_KEY", "SCANAPP_DEBUG"):
    os.environ.pop(_v, None)

from config import config          # noqa: E402
from app import create_app, db     # noqa: E402

SERVICE = os.path.join(ROOT, "deploy", "scanapp.service")
SERVICE_NGINX = os.path.join(ROOT, "deploy", "scanapp-behind-nginx.service")
NGINX = os.path.join(ROOT, "deploy", "nginx-scanapp.conf")
README = os.path.join(ROOT, "README.md")

PAYLOAD = "3225`237`8156`0"
PAYLOAD_2 = "3225`238`8156`0"


class LogCatcher(logging.Handler):
    """Collects records so tests can assert a failure was actually reported."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self):
        return "\n".join(r.getMessage() for r in self.records)

    def at_least(self, level):
        return [r for r in self.records if r.levelno >= level]


class Base(unittest.TestCase):
    """Fresh app, fresh demo state, capture redirected to a temp directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scanapp_test_")
        self._real_capture = db._DEMO_CAPTURE
        self._real_connect = db._connect
        self._real_dsn = config.DB_DSN
        db._DEMO_CAPTURE = os.path.join(self.tmp, "scans_captured.jsonl")
        db._DEMO["scans"] = []
        db._DEMO["seen"] = set()

        self.logs = LogCatcher()
        self.dblog = logging.getLogger("app.db")
        self.apilog = logging.getLogger("app.api")
        for lg in (self.dblog, self.apilog):
            lg.addHandler(self.logs)
            lg.setLevel(logging.DEBUG)

        self.app = create_app()
        self.c = self.app.test_client()

    def tearDown(self):
        db._DEMO_CAPTURE = self._real_capture
        db._connect = self._real_connect
        config.DB_DSN = self._real_dsn
        for lg in (self.dblog, self.apilog):
            lg.removeHandler(self.logs)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def capture_lines(self):
        if not os.path.exists(db._DEMO_CAPTURE):
            return []
        with io.open(db._DEMO_CAPTURE, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def scan(self, raw, station="saw5", **kw):
        body = {"station": station, "raw": raw, "client_scan_id": "t"}
        body.update(kw)
        return self.c.post("/api/scan", json=body)

    def break_database(self):
        """Make every DB call fail without touching a network or a driver."""
        config.DB_DSN = "TEST-ONLY-NOT-A-REAL-DSN"

        def _boom(*a, **k):
            raise RuntimeError(
                "simulated failure: could not open a connection to SQL Server "
                "at 10.0.0.21,1433 database FVMaster uid=sa"
            )

        db._connect = _boom


# ======================================================================
# 1. Deployment artefacts
# ======================================================================

class ServiceFileChecks(object):
    """
    Shared assertions for both unit-file variants.

    PATH and EXPECTED_BIND are set by the subclasses. Everything else must hold
    for either variant, because the hazards are the same in both.
    """

    PATH = None
    EXPECTED_BIND = None

    @classmethod
    def setUpClass(cls):
        cls.raw = io.open(cls.PATH, encoding="utf-8").read()
        cls.active = [l for l in cls.raw.split("\n")
                      if l.strip() and not l.strip().startswith("#")]

    def test_workers_is_exactly_one(self):
        """Demo mode is configured for exactly one Gunicorn worker."""
        self.assertEqual(re.findall(r"--workers\s+(\d+)", self.raw), ["1"])

    def test_runtimedirectory_absent(self):
        """RuntimeDirectory must never be an active directive: it is shared."""
        for line in self.active:
            self.assertNotIn("RuntimeDirectory", line)

    def test_runtimedirectory_hazard_is_documented(self):
        self.assertIn("RuntimeDirectory", self.raw)

    def test_restartsec_present(self):
        self.assertIn("RestartSec=5", self.active)

    def test_no_other_appx_app_referenced(self):
        joined = " ".join(self.active)
        for other in ("labelapp", "locationapp", "signapp", "logoapp"):
            self.assertNotIn(other, joined)

    def test_no_comment_inside_a_line_continuation(self):
        """
        A comment between continuation backslashes is parsed differently across
        systemd versions. Regression guard: this exact defect was introduced
        during remediation and would have produced a unit that fails to start.
        """
        continued = False
        for i, line in enumerate(self.raw.split("\n"), 1):
            if continued and line.strip().startswith("#"):
                self.fail("comment inside a line continuation at line %d: %r"
                          % (i, line))
            continued = line.rstrip().endswith("\\")

    def test_execstart_argv_is_sane(self):
        """Join the continuations and check the command systemd would run."""
        joined = re.sub(r"\\\s*\n\s*", " ", self.raw)
        execs = [l for l in joined.split("\n") if l.startswith("ExecStart=")]
        self.assertEqual(len(execs), 1)
        argv = execs[0][len("ExecStart="):].split()
        self.assertEqual(argv[0], "/var/www/apps/scanapp/venv/bin/gunicorn")
        self.assertEqual(argv[-1], "wsgi:application")
        self.assertIn("--workers", argv)
        self.assertEqual(argv[argv.index("--workers") + 1], "1")
        self.assertEqual(argv[argv.index("--bind") + 1], self.EXPECTED_BIND)
        for token in argv:
            self.assertFalse(token.startswith("#"),
                             "comment text leaked into argv: %r" % token)

    def test_verified_identity_preserved(self):
        for directive in (
            "User=www-data",
            "Group=www-data",
            "WorkingDirectory=/var/www/apps/scanapp",
            'Environment="PATH=/var/www/apps/scanapp/venv/bin"',
            "Restart=always",
        ):
            self.assertIn(directive, self.active)


class TestServiceFilePort(ServiceFileChecks, unittest.TestCase):
    """deploy/scanapp.service, the variant being deployed now."""

    PATH = SERVICE
    EXPECTED_BIND = "0.0.0.0:8081"

    def test_binds_tcp_8081(self):
        self.assertIn("--bind 0.0.0.0:8081", self.raw)

    def test_does_not_touch_the_shared_socket_directory(self):
        """
        The strongest isolation property of this variant. It must not reference
        /run/gunicorn at all, in any active directive: that directory is shared
        by all four production apps.
        """
        for line in self.active:
            self.assertNotIn("/run/gunicorn", line)

    def test_port_does_not_collide(self):
        """
        8081 confirmed free by the audit of 20 August 2026. The box listens on
        22, 53, 555, 556, 8080, 10000 and 10050.
        """
        ports = set(re.findall(r"--bind\s+0\.0\.0\.0:(\d+)", self.raw))
        self.assertEqual(ports, {"8081"})
        for taken in ("8080", "10000", "10050", "22"):
            self.assertNotIn("0.0.0.0:%s" % taken, self.raw)


class TestServiceFileBehindNginx(ServiceFileChecks, unittest.TestCase):
    """deploy/scanapp-behind-nginx.service, kept for the later move."""

    PATH = SERVICE_NGINX
    EXPECTED_BIND = "unix:/run/gunicorn/scanapp.sock"

    def test_only_scanapp_socket_referenced(self):
        socks = sorted(set(re.findall(r"/run/gunicorn/[a-z0-9]+\.sock", self.raw)))
        self.assertEqual(socks, ["/run/gunicorn/scanapp.sock"])

class TestNginxFragment(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = io.open(NGINX, encoding="utf-8").read()
        # Comments legitimately quote the house style, which names labelapp.sock.
        # Only active directives may reference another application's socket.
        cls.active = "\n".join(l for l in cls.raw.split("\n")
                               if l.strip() and not l.strip().startswith("#"))

    def test_only_scanapp_socket(self):
        socks = sorted(set(re.findall(r"/run/gunicorn/[a-z0-9]+\.sock", self.active)))
        self.assertEqual(socks, ["/run/gunicorn/scanapp.sock"])

    def test_no_other_app_in_active_directives(self):
        for other in ("labelapp", "locationapp", "signapp", "logoapp"):
            self.assertNotIn(other, self.active)

    def test_single_location_block_and_it_is_scanapp(self):
        locs = re.findall(r"^\s*location\s+([^\s{]+)", self.raw, re.M)
        self.assertEqual(locs, ["/scanapp/"])

    def test_script_name_header_present(self):
        self.assertIn("X-Script-Name     /scanapp", self.raw)

    def test_proxy_headers_restated(self):
        """nginx discards server-level headers if a location sets any."""
        for h in ("Host", "X-Forwarded-For", "X-Forwarded-Proto", "X-Script-Name"):
            self.assertIn(h, self.raw)


class TestReadme(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = io.open(README, encoding="utf-8").read()
        # Markdown wraps, so phrase checks run against whitespace-normalised text.
        cls.flat = re.sub(r"\s+", " ", cls.raw)

    def test_sites_available_not_sites_enabled_for_editing(self):
        self.assertIn("sites-available/appx-router", self.raw)
        self.assertNotIn("nano /etc/nginx/sites-enabled", self.raw)

    def test_chown_documented(self):
        self.assertIn("chown -R www-data:www-data /var/www/apps/scanapp", self.raw)

    def test_current_test_url_is_the_port_bound_one(self):
        """8081 at the root is what gets deployed first."""
        self.assertIn("http://10.50.0.101:8081/station/saw5", self.raw)
        self.assertNotIn("http://10.50.0.101:8081/station/saw5/", self.raw)
        self.assertIn("No trailing slash", self.raw)

    def test_nginx_url_documented_as_the_later_move(self):
        self.assertIn("http://10.50.0.101:8080/scanapp/station/saw5", self.raw)
        self.assertNotIn("http://10.50.0.101:8080/scanapp/station/saw5/", self.raw)

    def test_both_unit_files_documented(self):
        self.assertIn("deploy/scanapp.service", self.raw)
        self.assertIn("deploy/scanapp-behind-nginx.service", self.raw)
        self.assertIn("Do not install both", self.flat)

    def test_port_deployment_has_no_nginx_step(self):
        self.assertIn("There is no nginx step", self.flat)

    def test_security_posture_documented(self):
        self.assertIn("Security posture of the port-bound deployment", self.raw)
        for risk in ("No authentication on any endpoint", "No host firewall"):
            self.assertIn(risk, self.raw)

    def test_chown_typo_hazard_called_out(self):
        self.assertIn("highest-risk keystroke", self.flat)

    def test_port_rollback_states_nginx_untouched(self):
        self.assertIn("No nginx restore is needed because nginx was never touched",
                      self.flat)

    def test_both_urls_marked_unverified(self):
        self.assertIn("Unverified", self.raw)
        self.assertIn("https://appx.vinylbilt.com/scanapp/station/saw5", self.raw)

    def test_one_worker_rule_stated(self):
        self.assertIn("--workers 1", self.raw)
        self.assertIn("verified shared database", self.flat)

    def test_capture_file_rule_stated(self):
        self.assertIn("scans_captured.jsonl", self.raw)
        self.assertIn("stop scanning immediately", self.raw.lower())

    def test_four_stages_documented(self):
        for stage in ("Platform-path test", "Controlled demo capture test",
                      "Real SAW 5 capture session", "Production database deployment"):
            self.assertIn(stage, self.raw)

    def test_barcode_terminology_matches_parser(self):
        for name in ("unit_no", "master_key", "parent_key"):
            self.assertIn(name, self.raw)
        self.assertNotIn("| 2 | Line number within the cut list |", self.raw)

    def test_approved_claims_qualified(self):
        self.assertNotIn("the mock-up Anthony and Daniel approved", self.raw)

    def test_rollback_is_conditional_not_blanket_restart(self):
        self.assertIn("only** if step 5 shows it is unhealthy", self.flat)

    def test_rollback_triggers_listed(self):
        self.assertIn("Rollback triggers", self.raw)
        for trig in ("socket disappears", "nginx -t` fails",
                     "cannot create `scans_captured.jsonl`"):
            self.assertIn(trig, self.flat)


# ======================================================================
# 2. Application: routes, templates, static, prefix
# ======================================================================

class TestApplication(Base):

    def test_app_factory(self):
        self.assertTrue(callable(self.app.wsgi_app))

    def test_debug_is_off(self):
        self.assertFalse(self.app.debug)

    def test_all_routes_resolve(self):
        rules = {str(r) for r in self.app.url_map.iter_rules()}
        for expected in ("/", "/station/<station_key>", "/healthz",
                         "/api/scan", "/api/status/<station_key>",
                         "/api/recent", "/api/issue",
                         "/static/<path:filename>"):
            self.assertIn(expected, rules)

    def test_index_renders(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.data), 200)

    def test_station_renders(self):
        r = self.c.get("/station/saw5")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.data), 2000)

    def test_unknown_station_404_not_500(self):
        self.assertEqual(self.c.get("/station/nope").status_code, 404)

    def test_healthz(self):
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_static_assets_resolve(self):
        for f in ("scanapp.css", "scanapp.js"):
            r = self.c.get("/static/" + f)
            self.assertEqual(r.status_code, 200, f)
            self.assertGreater(len(r.data), 500, f)

    def test_script_name_produces_scanapp_paths(self):
        r = self.c.get("/station/saw5", headers={"X-Script-Name": "/scanapp"})
        body = r.data.decode("utf-8", "replace")
        self.assertIn("/scanapp/static/scanapp.css", body)
        self.assertIn("/scanapp/api/scan", body)
        self.assertIn("/scanapp/api/issue", body)

    def test_without_script_name_paths_are_root_relative(self):
        body = self.c.get("/station/saw5").data.decode("utf-8", "replace")
        self.assertIn("/static/scanapp.css", body)
        self.assertNotIn("/scanapp/", body)

    def test_statuswarn_element_present(self):
        body = self.c.get("/station/saw5").data.decode("utf-8", "replace")
        self.assertIn('id="statusWarn"', body)


# ======================================================================
# 3. Demo mode without SQL Server
# ======================================================================

class TestDemoMode(Base):

    def test_demo_mode_active_without_dsn(self):
        self.assertTrue(config.demo_mode)
        self.assertIsNone(config.DB_DSN)

    def test_scan_succeeds_with_no_database(self):
        r = self.scan(PAYLOAD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["state"], "ok")

    def test_successful_scan_is_durably_persisted(self):
        self.scan(PAYLOAD)
        rows = self.capture_lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["raw_scan_value"], PAYLOAD)

    def test_duplicate_detection(self):
        self.assertEqual(self.scan(PAYLOAD).get_json()["state"], "ok")
        self.assertEqual(self.scan(PAYLOAD).get_json()["state"], "dup")
        self.assertEqual(self.scan(PAYLOAD_2).get_json()["state"], "ok")
        self.assertEqual(len(self.capture_lines()), 3)

    def test_unparseable_payload_still_stored(self):
        r = self.scan("https://www.vinylbilt.com/")
        self.assertEqual(r.get_json()["state"], "unexpected")
        self.assertEqual(len(self.capture_lines()), 1)

    def test_empty_and_unknown_station_rejected(self):
        self.assertEqual(self.scan("").status_code, 400)
        self.assertEqual(self.scan(PAYLOAD, station="ghost").status_code, 400)

    def test_status_endpoint(self):
        self.scan(PAYLOAD)
        st = self.c.get("/api/status/saw5").get_json()
        self.assertTrue(st["status_available"])
        self.assertEqual(st["capture"]["scans"], 1)


# ======================================================================
# 4. Persistence failure must be loud, and must not claim success
# ======================================================================

class TestPersistenceFailure(Base):

    def break_persistence(self):
        db._DEMO_CAPTURE = os.path.join(
            self.tmp, "no_such_directory_" + os.urandom(4).hex(), "capture.jsonl")

    def test_failure_is_logged(self):
        self.break_persistence()
        self.scan(PAYLOAD)
        errors = self.logs.at_least(logging.ERROR)
        self.assertTrue(errors, "persistence failure produced no ERROR log")
        self.assertIn("DEMO CAPTURE WRITE FAILED", self.logs.text())

    def test_failure_is_not_reported_as_captured(self):
        self.break_persistence()
        r = self.scan(PAYLOAD)
        self.assertEqual(r.status_code, 503)
        self.assertNotEqual(r.get_json().get("state"), "ok")

    def test_in_memory_state_not_advanced_on_failure(self):
        self.break_persistence()
        self.scan(PAYLOAD)
        self.assertEqual(len(db._DEMO["scans"]), 0)
        self.assertEqual(len(db._DEMO["seen"]), 0)

    def test_recovery_after_failure(self):
        """Once the path is writable again, capture resumes and counts are right."""
        self.break_persistence()
        self.scan(PAYLOAD)
        db._DEMO_CAPTURE = os.path.join(self.tmp, "scans_captured.jsonl")
        r = self.scan(PAYLOAD)
        self.assertEqual(r.get_json()["state"], "ok",
                         "first successful scan must not be reported as a duplicate")
        self.assertEqual(len(self.capture_lines()), 1)

    def test_issue_does_not_report_success_when_save_fails(self):
        self.break_persistence()
        r = self.c.post("/api/issue", json={"station": "saw5", "raw": "jam"})
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.get_json()["ok"])

    def test_issue_reports_success_when_saved(self):
        r = self.c.post("/api/issue", json={"station": "saw5", "raw": "jam"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


# ======================================================================
# 5. No internal detail reaches the client
# ======================================================================

LEAKY = ("10.0.0.21", "10.60.0.21", "FVMaster", "uid=sa", "UID=sa",
         "Traceback", "pyodbc", "/var/www", "DRIVER=", "ODBC")


class TestNoInformationLeak(Base):

    def assert_clean(self, r, label):
        body = r.data.decode("utf-8", "replace")
        for token in LEAKY:
            self.assertNotIn(token, body, "%s leaked %r" % (label, token))

    def test_scan_failure_response_is_clean(self):
        db._DEMO_CAPTURE = os.path.join(self.tmp, "nope", "capture.jsonl")
        r = self.scan(PAYLOAD)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("detail", r.get_json())
        self.assert_clean(r, "/api/scan 503")

    def test_scan_failure_with_database_error_is_clean(self):
        self.break_database()
        r = self.scan(PAYLOAD)
        self.assertEqual(r.status_code, 503)
        self.assert_clean(r, "/api/scan 503 with db error")

    def test_healthz_failure_is_clean(self):
        self.break_database()
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assert_clean(r, "/healthz")
        self.assertFalse(r.get_json()["ok"])

    def test_issue_failure_response_is_clean(self):
        self.break_database()
        r = self.c.post("/api/issue", json={"station": "saw5", "raw": "jam"})
        self.assert_clean(r, "/api/issue 503")

    def test_detail_was_logged_even_though_not_returned(self):
        self.break_database()
        self.scan(PAYLOAD)
        self.assertTrue(self.logs.at_least(logging.ERROR),
                        "failure was hidden from the client AND from the log")


# ======================================================================
# 6. Database status failure must not take the page down
# ======================================================================

class TestStationPageSurvivesDatabaseFailure(Base):

    def test_station_page_does_not_500(self):
        self.break_database()
        r = self.c.get("/station/saw5")
        self.assertEqual(r.status_code, 200)

    def test_status_api_does_not_500(self):
        self.break_database()
        r = self.c.get("/api/status/saw5")
        self.assertEqual(r.status_code, 200)

    def test_status_reports_unavailable(self):
        self.break_database()
        st = self.c.get("/api/status/saw5").get_json()
        self.assertFalse(st["status_available"])
        self.assertTrue(st["status_message"])
        self.assertEqual(st["schedules"], [])
        self.assertIsNone(st["current"])

    def test_failure_is_logged(self):
        self.break_database()
        self.c.get("/api/status/saw5")
        self.assertTrue(self.logs.at_least(logging.ERROR))

    def test_unexpected_columns_do_not_500(self):
        """View exists but has different column names."""
        class Row(object):
            SawFile = "x"          # deliberately missing ScheduleNo etc.

        class Cur(object):
            def execute(self, *a, **k):
                return self

            def fetchall(self):
                return [Row()]

        class Conn(object):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cur()

        config.DB_DSN = "TEST-ONLY-NOT-A-REAL-DSN"
        db._connect = lambda *a, **k: Conn()
        r = self.c.get("/api/status/saw5")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["status_available"])


# ======================================================================
# 7. Timestamps
# ======================================================================

class TestTimestamps(Base):

    def test_utcnow_is_timezone_aware(self):
        now = db._utcnow()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), datetime.timedelta(0))

    def test_no_deprecated_utcnow_in_source(self):
        """AST walk, so docstrings and comments cannot mask or fake a call."""
        import ast
        for name in ("app/db.py", "app/api.py", "app/routes.py",
                     "app/scan_parser.py", "config.py", "wsgi.py"):
            with io.open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Attribute) and fn.attr == "utcnow":
                        self.fail("%s calls deprecated utcnow() at line %d"
                                  % (name, node.lineno))

    def test_capture_format_unchanged(self):
        """External format stays naive UTC, as already written to the file."""
        self.scan(PAYLOAD)
        ts = self.capture_lines()[0]["scanned_at"]
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")

    def test_naive_utc_conversion(self):
        aware = datetime.datetime(2026, 8, 20, 17, 5, 0,
                                  tzinfo=datetime.timezone.utc)
        self.assertIsNone(db._naive_utc(aware).tzinfo)
        self.assertEqual(db._naive_utc(aware).hour, 17)


# ======================================================================
# 8. Safety guards on the suite itself
# ======================================================================

class TestSuiteSafety(Base):

    def test_no_real_dsn_is_ever_set(self):
        self.break_database()
        self.assertNotIn("10.0.0.21", config.DB_DSN)
        self.assertNotIn("10.60.0.21", config.DB_DSN)
        self.assertIn("TEST-ONLY", config.DB_DSN)

    def test_pyodbc_not_exercised(self):
        """The SQL path is never executed for real by this suite."""
        self.break_database()
        with self.assertRaises(RuntimeError):
            db._connect()

    def test_real_capture_file_untouched(self):
        self.assertNotEqual(db._DEMO_CAPTURE, self._real_capture)
        self.assertIn(self.tmp, db._DEMO_CAPTURE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
