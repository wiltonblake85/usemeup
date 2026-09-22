#!/usr/bin/env python3
"""
statusline.py - rate limits from Claude Code's own status line, with no credential.

Claude Code pipes a JSON document to whatever command is configured as its
status line (https://code.claude.com/docs/en/statusline). For Pro and Max
subscribers that document carries

    "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                    "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}

So the figures can be had without reading the Keychain and without any network
call: Claude Code hands them over. This module never touches either.

Two halves:

  capture()  runs as the status line command (`usemeup statusline`). It saves
             the rate_limits block to DROP_PATH and prints one line.
  read()     is what the server calls. It turns the drop file into the same
             window shape rate_limits._shape_json() produces.

What this source cannot do, stated here so nobody has to discover it:

  * It has no model-scoped weekly window. Only the usage endpoint reports
    "7-day window, <model> only", and that is often the binding one.
  * It is only as fresh as the last Claude Code turn on THIS machine. Usage
    from cloud sessions or claude.ai moves the real number without moving this
    one until the next local turn. read() therefore reports when the figures
    were captured, and the server files the sample under that time, never under
    "now". A reading that is older than MAX_AGE is refused rather than shown as
    current.
  * rate_limits is absent until the first API response of a session, and
    absent entirely on API-key billing.
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile

from . import config

DROP_PATH = os.path.join(config.DB_DIR, "statusline.json")
# The weekly figure is still useful hours later; the 5-hour one is not. Past
# this age the honest answer is "no current reading", not an old number.
MAX_AGE = 6 * 3600

# Status line key -> the key the usage endpoint uses for the same window, so
# the sample history is one continuous series whichever source filled it.
KEYS = {"five_hour": "session", "seven_day": "weekly_all"}
NAMES = {"session": "5-hour session window", "weekly_all": "7-day window, all models"}
SECONDS = {"session": 5 * 3600, "weekly_all": 7 * 86400}


def command():
    """The status line command that works for THIS install.

    From the app bundle there is no `usemeup` on PATH, so the instruction has
    to name the bundled executable, or it tells people to run something that
    does not exist.
    """
    if getattr(sys, "frozen", False):
        return '"%s" statusline' % sys.executable
    return "usemeup statusline"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(epoch):
    try:
        n = float(epoch)
    except (TypeError, ValueError):
        return None
    if n > 1e11:            # milliseconds, in case the unit ever changes
        n /= 1000.0
    return datetime.datetime.fromtimestamp(n, datetime.timezone.utc).isoformat()


def extract(doc):
    """The windows in one status line document: {key: {used_pct, resets_at}}.

    Tolerant on purpose. Each window may be independently absent, and a window
    with no usable percentage is skipped rather than recorded as zero.
    """
    out = {}
    rl = (doc or {}).get("rate_limits")
    if not isinstance(rl, dict):
        return out
    for src, key in KEYS.items():
        w = rl.get(src)
        if not isinstance(w, dict):
            continue
        try:
            pct = float(w.get("used_percentage"))
        except (TypeError, ValueError):
            continue
        out[key] = {"used_pct": round(max(0.0, pct), 1), "resets_at": _iso(w.get("resets_at"))}
    return out


def save(windows, path=None, now=None):
    """Write the drop file atomically. Returns True if something was written.

    An empty reading never overwrites a good one: a fresh session has no
    rate_limits until its first response, and that must not blank the file.
    """
    if not windows:
        return False
    path = path or DROP_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {"captured_at": (now or _now()).isoformat(), "windows": windows}
    fd, tmp = tempfile.mkstemp(prefix=".statusline-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(body, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def line(windows):
    """The text shown in Claude Code's status line."""
    parts = []
    for key, label in (("session", "5h"), ("weekly_all", "7d")):
        if key in windows:
            parts.append("%s %d%%" % (label, round(windows[key]["used_pct"])))
    return " · ".join(parts)


def capture(text, passthrough=None, path=None):
    """Handle one status line invocation. Returns the line to print.

    passthrough is an existing status line command to keep: it receives the
    same stdin, and its output is printed first with ours appended. A status
    line must never break Claude Code, so every failure here degrades to
    printing less rather than raising.
    """
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError:
        doc = {}
    windows = extract(doc)
    try:
        save(windows, path)
    except Exception:
        pass
    ours = line(windows)
    theirs = ""
    if passthrough:
        try:
            out = subprocess.run(passthrough, shell=True, input=text, capture_output=True,
                                 text=True, timeout=5)
            theirs = (out.stdout or "").rstrip("\n")
        except Exception:
            theirs = ""
    if theirs and ours:
        return "%s  %s" % (theirs, ours)
    return theirs or ours


def read(path=None, now=None, max_age=MAX_AGE):
    """The drop file as a probe()-shaped result. Never raises.

    ok is False, with a reason, when there is no file, when it is older than
    max_age, or when every window in it has already reset.
    """
    path = path or DROP_PATH
    now = now or _now()
    base = {"ok": False, "source": "claude code status line", "windows": [], "raw": {},
            "strategy": "status line"}
    try:
        with open(path) as f:
            body = json.load(f)
        captured = datetime.datetime.fromisoformat(body["captured_at"])
        raw = body.get("windows") or {}
    except FileNotFoundError:
        base.update({"reason": "no_statusline",
                     "error": "No status line reading yet. In ~/.claude/settings.json set "
                              "\"statusLine\": {\"type\": \"command\", \"command\": %s}, "
                              "then send one message in Claude Code." % json.dumps(command())})
        return base
    except Exception as e:
        base.update({"reason": "no_statusline",
                     "error": "Could not read %s: %s" % (path, type(e).__name__)})
        return base

    age = (now - captured).total_seconds()
    if age > max_age:
        base.update({"reason": "statusline_stale", "captured_at": captured.isoformat(),
                     "error": "The last status line reading is %.1f hours old. It updates on "
                              "each Claude Code turn on this machine." % (age / 3600.0)})
        return base

    windows = []
    for key in ("session", "weekly_all"):
        w = raw.get(key)
        if not w:
            continue
        secs = None
        if w.get("resets_at"):
            try:
                secs = int((datetime.datetime.fromisoformat(w["resets_at"]) - now).total_seconds())
            except ValueError:
                secs = None
        if secs is not None and secs <= 0:
            continue        # that window has reset since; its figure describes the past
        windows.append({
            "key": key, "name": NAMES[key], "used_pct": float(w.get("used_pct") or 0),
            "status": None, "binding": False, "resets_at": w.get("resets_at"),
            "resets_in_seconds": secs, "window_seconds": SECONDS[key],
            "segments": 7 if key == "weekly_all" else None,
        })
    if not windows:
        base.update({"reason": "statusline_stale", "captured_at": captured.isoformat(),
                     "error": "Every window in the last status line reading has since reset."})
        return base

    base.update({"ok": True, "windows": windows, "raw": raw,
                 "checked_at": captured.isoformat(), "sample_ts": captured.isoformat(),
                 "age_seconds": int(age),
                 "attempts": [{"strategy": "status line", "status": "file",
                               "windows_found": len(windows), "detail": ""}]})
    return base


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="usemeup statusline")
    ap.add_argument("--passthrough", help="an existing status line command to keep running")
    args = ap.parse_args(argv)
    try:
        text = sys.stdin.read()
    except Exception:
        text = ""
    print(capture(text, args.passthrough))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
