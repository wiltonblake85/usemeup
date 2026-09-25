#!/usr/bin/env python3
"""
server.py - serves the dashboard on localhost only.

Binds 127.0.0.1, so nothing outside this machine can reach it.

  GET /             index.html
  GET /api/usage    aggregates from the local index (no network)
  GET /api/limits   live rate-limit state (reads Keychain, calls api.anthropic.com;
                    or, with USEMEUP_SOURCE=statusline, reads a local file only)
  GET /api/panel    the two weekly burn-up charts, pace already applied
  GET /api/ingest   force a re-scan of the transcript folders
  GET /api/export   everything as one JSON download
  GET /api/ping     who is serving: {"app": "usemeup", pid, source, auto_refresh}.
                    Touches no cache, so a client can ask "is this port ours?"
                    without paying for a rebuild.

Starting up claims the port FIRST and only then scans transcripts and starts
the sampler. If the port is taken the process waits in standby, retrying the
bind every BIND_RETRY seconds, and does nothing else. This matters because two
things can start a server at login: the LaunchAgent (KeepAlive) and the menu bar
app, which spawns its bundled copy when nothing answers. The old order (scan,
start the sampler, then bind) meant a process that was going to lose the port
did the expensive work first and then died, and launchd restarted it every ten
seconds. On 2026-09-25 that loop had run 3,737 times in 14.5 hours at roughly
half a CPU core. Waiting instead of exiting also means the LaunchAgent takes
over within BIND_RETRY seconds of the other server going away.

It also keeps ~/.usemeup/status.json current (see status.py) for readers that
would rather open a file than call this server.

While the server runs it also samples the rate-limit windows itself, once every
SAMPLE_EVERY seconds, so the daily view is not blind whenever the page is closed.
It goes through the same cache, TTL and 429 back-off as the page, so the total
call rate to api.anthropic.com is unchanged: at most one probe per five minutes.
The same tick re-scans the transcript folders (unchanged files are skipped, so
it costs almost nothing), so the index is current the moment the page opens
rather than however old the last page load was.
"""
import errno
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))

from . import burn
from . import config
from . import daily
from . import history
from . import panel
from . import parse_usage
from . import rate_limits
from . import status
from . import store

PORT = config.PORT
_cache = {"usage": None, "usage_at": 0, "limits": None, "limits_at": 0, "ingest": None,
          "priors": None, "priors_at": 0}
_lock = threading.Lock()

TTL_USAGE, TTL_INGEST = 60, 120
# History moves over days. Rebuilding it reads the whole sample table.
TTL_PRIORS = 900
# The usage endpoint rate limits an over-eager client; 45s earned a 429. These
# numbers barely move minute to minute, so poll gently and back off hard.
TTL_LIMITS = 300
TTL_LIMITS_ERROR = 60
BACKOFF_429 = [300, 600, 1200, 1800]
_last_good = [None]
_429_streak = [0]
_last_ingest = [0.0]


def _maybe_ingest(force=False):
    if not force and time.time() - _last_ingest[0] < TTL_INGEST:
        return _cache["ingest"]
    with _lock:
        # Someone else may have finished a scan while this caller waited for
        # the lock (the startup scan, most often); one scan per TTL is enough.
        if not force and time.time() - _last_ingest[0] < TTL_INGEST:
            return _cache["ingest"]
        try:
            _cache["ingest"] = store.ingest()
            if (_cache["ingest"] or {}).get("new_calls"):
                _cache["usage"] = None      # aggregates are stale; rebuild on next read
        except Exception as e:
            _cache["ingest"] = {"error": "%s: %s" % (type(e).__name__, e)}
        _last_ingest[0] = time.time()
    return _cache["ingest"]


def _priors():
    """What a full window of each kind usually consumes, from the sample history.

    The projection leans on this while a window is too young to speak for
    itself. The current window is excluded by id so it never helps predict
    itself, and the whole thing is cached: it moves over days, not minutes.
    """
    if _cache["priors"] is not None and time.time() - _cache["priors_at"] < TTL_PRIORS:
        return _cache["priors"]
    out = {}
    try:
        wins = (_limits() or {}).get("windows") or []
        secs = {w.get("key"): w.get("window_seconds") for w in wins}
        current = {w.get("key"): history.window_id(w.get("resets_at")) for w in wins}
        samples, _names = store.all_limit_samples("2000-01-01T00:00:00+00:00")
        out = history.priors(samples, secs, current)
    except Exception:
        out = {}      # no history is a valid answer; the panel says so
    _cache["priors"], _cache["priors_at"] = out, time.time()
    return out


def _usage():
    _maybe_ingest()
    if not _cache["usage"] or time.time() - _cache["usage_at"] > TTL_USAGE:
        d = parse_usage.build()
        d["ingest"] = _cache["ingest"]
        _cache["usage"], _cache["usage_at"] = d, time.time()
    return _cache["usage"]


_limits_lock = threading.Lock()


def _limits():
    # Serialised: the page, a second tab and the sampler thread can all ask at
    # once, and only one of them should ever probe the API for the same answer.
    with _limits_lock:
        return _limits_unlocked()


def _limits_unlocked():
    cur, age = _cache["limits"], time.time() - _cache["limits_at"]
    if cur:
        if cur.get("ok"):
            ttl = TTL_LIMITS
        elif cur.get("reason") == "rate_limited":
            ttl = BACKOFF_429[min(_429_streak[0], len(BACKOFF_429) - 1)]
        else:
            ttl = TTL_LIMITS_ERROR
        if age < ttl:
            return cur

    try:
        d = rate_limits.probe()
    except Exception as e:
        d = {"ok": False, "windows": [], "raw": {}, "reason": "exception",
             "error": "%s: %s" % (type(e).__name__, e)}

    if d.get("ok"):
        _429_streak[0] = 0
        try:
            store.record_limit_sample(d.get("windows"), ts=d.get("sample_ts"))
        except Exception:
            pass          # sampling is best-effort; never break the panel over it
        _last_good[0] = dict(d)
    elif d.get("reason") == "rate_limited":
        _429_streak[0] += 1

    # A failed refresh should not blank a panel that had good numbers a moment ago.
    if not d.get("ok") and _last_good[0]:
        prev = dict(_last_good[0])
        prev.update({"ok": True, "stale": True, "stale_reason": d.get("error"),
                     "stale_kind": d.get("reason"), "stale_since": prev.get("checked_at")})
        for w in prev.get("windows", []):
            if w.get("resets_at"):
                w["resets_in_seconds"] = rate_limits._secs_until(w["resets_at"])
        d = prev

    if d.get("windows"):
        try:
            burn.attach(d["windows"], store.limit_samples)
        except Exception as e:
            d["burn_error"] = "%s: %s" % (type(e).__name__, e)
        try:
            daily.attach(d["windows"], store.limit_samples)
        except Exception as e:
            d["daily_error"] = "%s: %s" % (type(e).__name__, e)

    _cache["limits"], _cache["limits_at"] = d, time.time()
    return d


SAMPLE_EVERY = TTL_LIMITS    # one probe per cache lifetime; never more than the page alone


def _write_status():
    """Rewrite ~/.usemeup/status.json from the caches the page already uses.

    Best-effort: a status file that cannot be written must never stop the
    sampler, and the error is kept where /api/limits can show it.
    """
    try:
        status.write(status.build(_usage(), _limits(), _priors()))
        _cache["status_error"] = None
    except Exception as e:
        _cache["status_error"] = "%s: %s" % (type(e).__name__, e)


def _sampler():
    """Keep the sample history and the transcript index filling while the page is closed.

    _limits() owns every rule about when a probe is allowed (cache TTL, error
    TTL, 429 back-off), so this loop just asks on a timer and lets it decide.
    Failures are already recorded in the cache; nothing to do here but wait.
    _maybe_ingest() has its own TTL and skips unchanged files, so calling it
    here keeps the index fresh at no real cost.
    """
    _write_status()           # at start, so a reader is not blind for five minutes
    while True:
        time.sleep(SAMPLE_EVERY)
        try:
            _maybe_ingest()
        except Exception:
            pass
        try:
            _limits()
        except Exception:
            pass
        _write_status()


def start_sampler():
    t = threading.Thread(target=_sampler, name="usemeup-sampler", daemon=True)
    t.start()
    return t


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, extra=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.send_header("cache-control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/ping":
                return self._send(200, json.dumps(ping()), "application/json")
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/usage":
                return self._send(200, json.dumps(_usage(), default=str), "application/json")
            if path == "/api/limits":
                return self._send(200, json.dumps(_limits(), default=str), "application/json")
            if path == "/api/panel":
                # Both halves come from the cache _limits() and _usage() already
                # own, so a client polling this adds no api.anthropic.com traffic
                # and no extra ingest, however often it asks.
                return self._send(200, json.dumps(
                    panel.build(_usage(), _limits(), priors=_priors()), default=str),
                    "application/json")
            if path == "/api/menubar":
                # Same model as /api/panel, minus the plotted series. A menu
                # bar polls on a timer forever, so it gets its own shape
                # rather than pulling 9 KB of chart points every minute.
                return self._send(200, json.dumps(
                    panel.menubar(_usage(), _limits(), _priors()), default=str),
                    "application/json")
            if path == "/api/ingest":
                _cache["usage"] = None
                return self._send(200, json.dumps(_maybe_ingest(force=True)), "application/json")
            if path == "/api/export":
                payload = {"usage": _usage(), "limits": _limits(),
                           "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                return self._send(
                    200, json.dumps(payload, default=str, indent=2), "application/json",
                    {"content-disposition": 'attachment; filename="usemeup-export.json"'})
            self._send(404, "not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass          # the browser went away mid-response; nothing to tell it
        except Exception as e:
            try:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}),
                           "application/json")
            except OSError:
                pass      # dead socket; the error is already lost on the client side


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def ping():
    """Who this server is. Cheap on purpose: no cache, no disk, no network."""
    from . import __version__
    return {"app": config.APP, "version": __version__, "pid": os.getpid(),
            "source": config.SOURCE, "auto_refresh": config.AUTO_REFRESH}


# How often a server in standby tries the port again. A bind attempt costs
# nothing, so this sets how quickly the LaunchAgent takes over, not the load.
BIND_RETRY = 30


def occupant(port, timeout=1.0):
    """A short description of whatever holds 127.0.0.1:port.

    Asks /api/ping. Another UseMeUp answers with its pid; anything else (a
    Wrangler dev server on 8787, a server too old to have /api/ping) is
    "another program". Only used for the standby log line.
    """
    # No proxy: a system or env proxy must never be asked about our own loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/api/ping" % port, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(d, dict) and d.get("app") == config.APP:
            return "another UseMeUp server (pid %s)" % d.get("pid")
    except Exception:
        pass
    return "another program"


def bind(port, retry_every=None, log=None, stop=None):
    """Claim 127.0.0.1:port, waiting for it if it is taken. Returns the Server.

    Waits rather than exits, so a LaunchAgent with KeepAlive never loops. Logs
    once when it starts waiting and again only if the occupant changes, so a
    long standby is one line in the log, not thousands. `stop` is an optional
    threading.Event that ends the wait (returns None); tests use it.
    """
    retry_every = BIND_RETRY if retry_every is None else retry_every
    log = log or (lambda m: print(m, flush=True))
    said = None
    while True:
        try:
            return Server(("127.0.0.1", port), Handler)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
        who = occupant(port)
        if who != said:
            log("port %d is held by %s; standing by, trying again every %ss. "
                "Nothing is scanned or sampled until this process has the port."
                % (port, who, retry_every))
            said = who
        if stop is not None:
            if stop.wait(retry_every):
                return None
        else:
            time.sleep(retry_every)


def run(port=None, open_browser=True):
    """Claim the port, then scan, then sample, then serve. See the module doc."""
    port = port or PORT
    httpd = bind(port)
    print("port %d is ours (pid %d)." % (port, os.getpid()), flush=True)
    # Serve from this moment, so /api/ping answers during a long first scan and
    # the menu bar app can tell "ours, still scanning" from "nobody here". Data
    # requests made meanwhile wait on the scan's lock rather than failing.
    web = threading.Thread(target=httpd.serve_forever, name="usemeup-http", daemon=True)
    web.start()
    print("scanning transcripts…", flush=True)
    info = _maybe_ingest(force=True)
    if info and "error" not in info:
        print("  %s calls indexed (%s files read, %s unchanged, %ss)" % (
            format(info["total_calls"], ","), format(info["files_scanned"], ","),
            format(info["files_unchanged"], ","), info["seconds"]), flush=True)
    else:
        print("  ingest problem: %s" % (info or {}).get("error"), flush=True)
    print(config.banner(), flush=True)
    start_sampler()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d/" % port)).start()
    try:
        while web.is_alive():
            web.join(1.0)          # a timed join, so ctrl-c still lands here
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run(PORT, open_browser=True))
