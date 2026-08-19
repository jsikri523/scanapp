/* Operator screen behaviour.
 *
 * Three jobs:
 *   1. Catch what the wedge scanner types and send it to the server.
 *   2. Show the operator what happened, visually. Ear protection is
 *      mandatory in that bay so there is no audible confirmation.
 *   3. Never lose a scan when the Wi-Fi drops (open item Q7).
 */
(function () {
  "use strict";

  var CFG = window.SCANAPP;
  var $ = function (id) { return document.getElementById(id); };
  var pad = function (n) { return String(n).padStart(2, "0"); };

  var QUEUE_KEY = "scanapp.queue." + CFG.stationKey;

  var COPY = {
    ready:      { i: "●", w: "Ready to scan",       m: "Scan the label on the next piece." },
    ok:         { i: "✓", w: "Scan accepted",       m: "" },
    dup:        { i: "↻", w: "Seen before",         m: "" },
    unexpected: { i: "?",      w: "Label not recognised", m: "" },
    wrongcode:  { i: "!",      w: "Wrong code on label",  m: "That code does not identify the piece. Scan the other code on the same label." },
    error:      { i: "✕", w: "Scan failed",         m: "Scan the label again." },
    queued:     { i: "↑", w: "Saved on the tablet", m: "No network. It will send automatically. Carry on scanning." },
    reported:   { i: "!",      w: "Issue reported",      m: "Your supervisor has been told. Carry on scanning." },
    advance:    { i: "✓", w: "Schedule complete",   m: "" }
  };

  function nowStamp() {
    var t = new Date();
    return pad(t.getHours()) + ":" + pad(t.getMinutes()) + ":" + pad(t.getSeconds());
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) { return crypto.randomUUID(); }
    return "s-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function flash() {
    var p = $("stage");
    p.classList.remove("flash");
    void p.offsetWidth;
    p.classList.add("flash");
  }

  /* ---------------- rendering ---------------- */

  function setState(state, payload) {
    var c = COPY[state];
    document.body.setAttribute("data-state", state);
    $("icon").textContent = c.i;
    $("word").textContent = c.w;
    $("stamp").textContent = (state === "ready") ? "—" : nowStamp();

    if (state === "ok" && payload) {
      paintScan(payload);
    } else if (state === "dup" && payload) {
      // Never tell the operator to skip. A repeat payload may be a genuine
      // second piece of the same window, and we do not yet know. The scan is
      // saved either way, so the safe instruction is to carry on.
      paintScan(payload);
      $("msg").textContent = "Unit " + (payload.unit_no || "this one") +
        " has been scanned before at this station. Saved anyway. Carry on scanning.";
    } else if (state === "unexpected") {
      $("msg").textContent = "The scan was saved but the label could not be read. " +
        "Set the piece aside and tell your supervisor.";
    } else {
      $("msg").textContent = c.m;
    }
    setStatusLine(state, payload);
    flash();
  }
  window.setState = setState;

  function paintScan(s) {
    $("fOrder").textContent = s.schedule_no || s.order_no || "—";
    $("fUnit").textContent  = s.unit_no || "—";
    if (s.schedule_no) { $("hdrSched").textContent = s.schedule_no; }

    // The combination unit, only when there is one. FeneVision shows this as
    // a parent order-item, for example unit 523 holding units 61, 62, 275,
    // 521 and 522. Most units have no parent, so the row stays hidden.
    var hasParent = s.parent_key && s.parent_key !== "0";
    $("parentRow").hidden = !hasParent;
    if (hasParent) { $("fParent").textContent = s.parent_key; }

    // The unit is the biggest thing the barcode gives us that means anything
    // to a person. The bin would be better and is what the operator acts on,
    // but the barcode does not carry it and SAW 5's schedule files do not
    // either. It comes from FeneVision when the join exists.
    $("slotcue").textContent = s.bin_no ? "Rack in bin" : "Unit";
    $("slotbig").textContent = s.bin_no || s.unit_no || "OK";

    var bits = [];
    if (s.schedule_no) { bits.push("Schedule " + s.schedule_no); }
    if (hasParent)     { bits.push("in combo " + s.parent_key); }
    $("slotmeta").innerHTML = bits.join(" &nbsp;·&nbsp; ");
  }

  /* FeneVision Tracking prints one sentence across the bottom, in the shape
     "Unit 142 Complete on 2026-08-19 1:49 PM". Ours says what actually
     happened rather than claiming completion, because whether a scan
     completes anything is exactly what is not yet settled. */
  function setStatusLine(state, s) {
    var el = $("statusLine");
    if (!el) { return; }
    var t = new Date().toLocaleTimeString();
    if (state === "ok" && s) {
      el.textContent = "Unit " + (s.unit_no || "?") + " scanned at " + t + ".";
    } else if (state === "dup" && s) {
      el.textContent = "Unit " + (s.unit_no || "?") + " scanned again at " + t + ". Saved.";
    } else if (state === "unexpected") {
      el.textContent = "Label not recognised at " + t + ". Saved for review.";
    } else if (state === "queued") {
      el.textContent = "No network at " + t + ". Scan held on the tablet and will send.";
    } else if (state === "reported") {
      el.textContent = "Issue reported at " + t + ".";
    } else if (state === "error") {
      el.textContent = "Scan failed at " + t + ".";
    }
  }

  function paintStatus(st) {
    if (!st) { return; }

    // Scans taken and distinct payloads among them. Both are true and both
    // can be checked against the capture file. No denominator and no
    // percentage: progress against a schedule needs the day's real schedule
    // off the machine, and a decision on whether a scan counts a piece or a
    // window. Neither is settled, so neither is shown.
    if (st.capture) {
      $("cDone").textContent = st.capture.scans;
      $("cLeft").textContent = st.capture.units;
    } else if (st.current) {
      $("cDone").textContent = st.current.scanned;
      $("cLeft").textContent = st.current.total;
    }
  }

  function setConnection(online, queuedCount) {
    var el = $("conn");
    if (online && !queuedCount) {
      el.classList.remove("offline");
      el.lastChild.textContent = " Scanner ready";
    } else {
      el.classList.add("offline");
      el.lastChild.textContent = queuedCount
        ? " " + queuedCount + " waiting to send"
        : " No network";
    }
  }

  /* ---------------- offline queue ---------------- */
  /* A scan is never dropped. If the POST fails it goes to localStorage and
     is retried. Every scan carries a client id so a retry that actually
     did land the first time is harmless: the reporting view dedupes it. */

  function readQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); }
    catch (e) { return []; }
  }

  function writeQueue(q) {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); } catch (e) { /* full */ }
    setConnection(navigator.onLine, q.length);
  }

  function enqueue(body) {
    var q = readQueue();
    q.push(body);
    writeQueue(q);
  }

  function drainQueue() {
    var q = readQueue();
    if (!q.length) { setConnection(navigator.onLine, 0); return; }

    var body = q[0];
    fetch(CFG.scanUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok && r.status !== 400) { throw new Error("retry later"); }
      q.shift();
      writeQueue(q);
      if (q.length) { setTimeout(drainQueue, 400); }
    }).catch(function () {
      setConnection(false, q.length);
    });
  }

  /* ---------------- sending a scan ---------------- */

  function sendScan(raw) {
    var body = {
      station: CFG.stationKey,
      raw: raw,
      client_scan_id: uuid()
    };

    fetch(CFG.scanUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    }).then(function (res) {
      if (!res.ok) { throw new Error(res.data && res.data.message); }
      setState(res.data.state, res.data.scan);
      paintStatus(res.data.status);
      setConnection(true, readQueue().length);
      if (res.data.state === "ok") {
        setTimeout(function () {
          if (document.body.getAttribute("data-state") === "ok") { setState("ready"); }
        }, 6000);
      }
    }).catch(function () {
      // Do not tell the operator it failed. It is saved, it will send.
      enqueue(body);
      setState("queued");
    });
  }

  /* ---------------- wedge scanner input ---------------- */
  /* The scanner behaves as a keyboard: it types the payload then sends
     Enter. We keep a hidden input focused so the characters always land
     somewhere, and read it on Enter. */

  var sink = $("sink");

  function refocus() {
    if (document.activeElement !== sink && !/^(BUTTON)$/.test(document.activeElement.tagName)) {
      sink.focus({ preventScroll: true });
    }
  }

  /* Demo-mode diagnostics. Shows whether keystrokes are reaching the page
     at all, which is otherwise invisible when the input is off screen. */
  var dbg = {
    focus: $("dbgFocus"), chars: $("dbgChars"),
    buf: $("dbgBuf"), posts: $("dbgPosts"), lastkey: $("dbgKey"),
    charCount: 0, postCount: 0
  };
  var hasDbg = !!dbg.focus;

  function dbgTick() {
    if (!hasDbg) { return; }
    var ok = document.hasFocus() && document.activeElement === sink;
    dbg.focus.textContent = !document.hasFocus() ? "WINDOW NOT FOCUSED"
                          : (document.activeElement === sink ? "ready" : "elsewhere");
    dbg.focus.className = ok ? "" : "bad";
  }
  if (hasDbg) { setInterval(dbgTick, 400); dbgTick(); }

  /* Submitting a scan.
   *
   * We do NOT rely on the scanner sending Enter. Testing on 17 August 2026
   * showed a scanner that produces line breaks in Notepad but whose
   * terminator never reaches Chrome as an Enter keydown, so the buffer
   * filled up and nothing was ever sent.
   *
   * A wedge scanner types far faster than a person. So the reliable
   * terminator is the pause after the burst: if no character arrives for
   * QUIET_MS, the scan is complete. Enter and Tab still submit immediately
   * when they do arrive, so a correctly configured scanner is no slower.
   *
   * This also means the app works whatever suffix the scanner is set to,
   * which matters because the tablet may not be configured the same way as
   * the test machine.
   */
  var QUIET_MS = 120;
  var quietTimer = null;

  function submitBuffer() {
    clearTimeout(quietTimer);
    quietTimer = null;
    var raw = sink.value.replace(/[\r\n\t]+/g, "").trim();
    sink.value = "";
    if (hasDbg) {
      dbg.buf.textContent = "(empty)";
      if (raw) { dbg.postCount++; dbg.posts.textContent = dbg.postCount; }
    }
    if (raw) { sendScan(raw); }
  }

  sink.addEventListener("input", function () {
    if (hasDbg) {
      dbg.charCount++;
      dbg.chars.textContent = dbg.charCount;
      dbg.buf.textContent = sink.value || "(empty)";
    }
    clearTimeout(quietTimer);
    quietTimer = setTimeout(submitBuffer, QUIET_MS);
  });

  sink.addEventListener("keydown", function (e) {
    if (hasDbg && dbg.lastkey) { dbg.lastkey.textContent = e.key; }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      submitBuffer();
    }
  });

  document.addEventListener("click", refocus);
  setInterval(refocus, 1500);
  refocus();

  /* ---------------- buttons ---------------- */

  window.reportIssue = function () {
    fetch(CFG.issueUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station: CFG.stationKey, raw: "" })
    }).catch(function () { /* recorded best effort */ });
    setState("reported");
    setTimeout(function () { setState("ready"); }, 5000);
  };

  window.backToReady = function () { setState("ready"); };

  /* ---------------- polling and clock ---------------- */

  function poll() {
    fetch(CFG.statusUrl)
      .then(function (r) { return r.json(); })
      .then(function (st) {
        paintStatus(st);
        setConnection(true, readQueue().length);
      })
      .catch(function () { setConnection(false, readQueue().length); });
  }

  setInterval(function () { $("clock").textContent = nowStamp(); }, 1000);
  $("clock").textContent = nowStamp();

  setInterval(poll, CFG.refreshMs);
  setInterval(drainQueue, 10000);
  window.addEventListener("online", drainQueue);

  paintStatus(CFG.initialStatus);
  setConnection(navigator.onLine, readQueue().length);
  setState("ready");
  drainQueue();
})();
