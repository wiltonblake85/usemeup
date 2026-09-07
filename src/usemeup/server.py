#!/usr/bin/env python3
"""
server.py - serves the dashboard on localhost only.

Binds 127.0.0.1, so nothing outside this machine can reach it.

  GET /             index.html
  GET /api/usage    aggregates from the local index (no network)
  GET /api/limits   live rate-limit state (reads Keychain, calls api.anthropic.com)
  GET /api/ingest   force a re-scan of the transcript folders
  GET /api/export   everything as one JSON download
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))

from . import burn
from . import config
from . import parse_usage
from . import rate_limits
from . import store

PORT = config.PORT
_cache = {"usage": None, "usage_at": 0, "limits": None, "limits_at": 0, "ingest": None}
_lock = threading.Lock()

TTL_USAGE, TTL_INGEST = 60, 120
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
        try:
            _cache["ingest"] = store.ingest()
        except Exception as e:
            _cache["ingest"] = {"error": "%s: %s" % (type(e).__name__, e)}
        _last_ingest[0] = time.time()
    return _cache["ingest"]


def _usage():
    _maybe_ingest()
    if not _cache["usage"] or time.time() - _cache["usage_at"] > TTL_USAGE:
        d = parse_usage.build()
        d["ingest"] = _cache["ingest"]
        _cache["usage"], _cache["usage_at"] = d, time.time()
    return _cache["usage"]


def _limits():
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
            store.record_limit_sample(d.get("windows"))
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

    _cache["limits"], _cache["limits_at"] = d, time.time()
    return d


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
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/usage":
                return self._send(200, json.dumps(_usage(), default=str), "application/json")
            if path == "/api/limits":
                return self._send(200, json.dumps(_limits(), default=str), "application/json")
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
        except BrokenPipeError:
            pass
        except Exception as e:
            self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}),
                       "application/json")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print("scanning transcripts…", flush=True)
    info = _maybe_ingest(force=True)
    if info and "error" not in info:
        print("  %s calls indexed (%s files read, %s unchanged, %ss)" % (
            format(info["total_calls"], ","), format(info["files_scanned"], ","),
            format(info["files_unchanged"], ","), info["seconds"]), flush=True)
    else:
        print("  ingest problem: %s" % (info or {}).get("error"), flush=True)

    print(config.banner())
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:%d/" % PORT)).start()
    try:
        Server(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
