#!/usr/bin/env python3
"""
config.py - every path, flag and platform assumption in one auditable place.

Nothing here touches the network or a credential. If you are evaluating whether
this tool is safe to run, read this file and rate_limits.py; that is the whole
surface that reaches outside your own transcripts.

Environment variables:
  USEMEUP_PORT           port for the local server            (default 8787)
  USEMEUP_TZ             IANA zone for day bucketing          (default: your system zone)
  USEMEUP_DEMO=1         redact project and session names, for screenshots
  USEMEUP_AUTO_REFRESH=1 let the dashboard run `claude -p ok` to refresh an
                         expired token. OFF by default because it spends a
                         small number of your tokens. See README.
  USEMEUP_OFFLINE=1      never fetch live prices; use the cache or bundled table
  USEMEUP_DB             override the database location
"""
import datetime
import hashlib
import os
import sys

APP = "usemeup"
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- transcripts
# Claude Code CLI: one folder per directory you ran `claude` in.
CLI_ROOT = os.path.expanduser("~/.claude/projects")

# Cowork desktop: one nested tree per session, including subagent transcripts.
# This path is macOS-specific; the Linux/Windows equivalents are listed so the
# tool degrades to "CLI only" rather than silently reporting a partial total.
_COWORK_CANDIDATES = [
    "~/Library/Application Support/Claude/local-agent-mode-sessions",   # macOS
    "~/.config/Claude/local-agent-mode-sessions",                        # Linux
    os.path.join(os.environ.get("APPDATA", "~"), "Claude",
                 "local-agent-mode-sessions"),                           # Windows
]


def cowork_root():
    """First Cowork transcript root that exists, or None."""
    for c in _COWORK_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.isdir(p):
            return p
    return None


COWORK_ROOT = cowork_root()

# ---------------------------------------------------------------- storage
DB_DIR = os.path.expanduser("~/.%s" % APP)
DB_PATH = os.environ.get("USEMEUP_DB") or os.path.join(DB_DIR, "usage.db")
REFRESH_CWD = os.path.join(DB_DIR, "refresh")

# ---------------------------------------------------------------- runtime
PORT = int(os.environ.get("USEMEUP_PORT", "8787"))
DEMO = os.environ.get("USEMEUP_DEMO", "") not in ("", "0", "false", "False")
AUTO_REFRESH = os.environ.get("USEMEUP_AUTO_REFRESH", "") not in ("", "0", "false", "False")
# Skip the public pricing fetch and use the cached or bundled table instead.
OFFLINE = os.environ.get("USEMEUP_OFFLINE", "") not in ("", "0", "false", "False")


def local_tz():
    """The zone used to bucket calls into calendar days.

    Defaults to whatever this machine is set to, so a user in Berlin does not
    silently get days cut at US midnight. USEMEUP_TZ overrides it.
    """
    name = os.environ.get("USEMEUP_TZ")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            print("usemeup: unknown USEMEUP_TZ %r, falling back to system zone" % name,
                  file=sys.stderr)
    return datetime.datetime.now().astimezone().tzinfo


LOCAL_TZ = local_tz()


def local_day(ts):
    """Calendar day of an ISO timestamp in the user's own zone, not UTC."""
    if not ts:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:10]
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(LOCAL_TZ).date().isoformat()


# ---------------------------------------------------------------- exclusions
# This tool can make its own throwaway Claude calls to refresh an expired token
# (see rate_limits.refresh_via_claude_code). Those land in the normal transcript
# folders, so they must be excluded from counting or the tool inflates its own
# numbers. Defined once here because store.py and verify.py must agree exactly:
# when they disagreed, verify.py reported a false mismatch.
EXCLUDE_MARKERS = ("usemeup", "aiusage")


def excluded(path):
    """True if a transcript path is this tool's own noise rather than your work."""
    p = (path or "").lower()
    return any(m in p for m in EXCLUDE_MARKERS)


# ---------------------------------------------------------------- demo mode
# Screenshots of this dashboard would otherwise publish the names of every
# directory you have worked in. Demo mode keeps all the numbers real and
# replaces only the labels, deterministically, so a screenshot stays coherent.
_ADJ = ["amber", "brisk", "cobalt", "dusk", "ember", "flint", "glade", "harbor",
        "ivory", "juniper", "kestrel", "larch", "meadow", "nimbus"]
_NOUN = ["atlas", "beacon", "cipher", "delta", "engine", "forge", "gantry",
         "harbor", "index", "jetty", "kernel", "ledger", "mesa", "nexus"]


def redact(label, kind="project"):
    """Stable pseudonym for a path-like label. Same input, same output."""
    if not DEMO or not label:
        return label
    keep = {"Cowork (desktop)", "desktop scratch", "home directory", "cowork", "cli"}
    if label in keep:
        return label
    h = hashlib.sha256(("%s|%s" % (kind, label)).encode()).digest()
    name = "%s-%s" % (_ADJ[h[0] % len(_ADJ)], _NOUN[h[1] % len(_NOUN)])
    return "~/%s" % name if kind == "project" else name


def banner():
    lines = ["%s  ->  http://127.0.0.1:%d/" % (APP, PORT),
             "bound to 127.0.0.1 only. ctrl-c to stop."]
    if DEMO:
        lines.append("DEMO MODE: project and session names are redacted. Numbers are real.")
    if not COWORK_ROOT:
        lines.append("note: no Cowork transcript folder found; showing Claude Code CLI only.")
    lines.append("sampling rate limits every 5 min while running, so the daily view "
                 "keeps filling with the page closed.")
    lines.append("auto-refresh: %s" % ("ON (will run `claude -p ok` when the token expires)"
                                       if AUTO_REFRESH else
                                       "off (set USEMEUP_AUTO_REFRESH=1 to enable)"))
    if OFFLINE:
        lines.append("offline mode: no price fetch; using the cached or bundled table.")
    return "\n".join(lines)
