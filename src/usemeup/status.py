#!/usr/bin/env python3
"""
status.py - the rate-limit picture as a file, for anything that cannot or
should not speak HTTP to the server.

    ~/.usemeup/status.json

The server rewrites it on every sampler tick (every five minutes) and once at
start. Readers open a file; they need no port, no timeout, and no retry logic,
and a reader that runs while the server is down sees the last good picture with
its age stated rather than a connection error.

What is in it:

  schema             bumped only on a change that breaks a reader
  written_at         when this file was produced
  checked_at         when the figures were last true (the API reading)
  fresh_for_seconds  how old checked_at may be before a reader should stop
                     showing the figures. The policy lives here, not in each
                     reader, so every surface goes blank at the same moment.
  stale              the last probe failed and these are the last good figures
  ok, hours_per_day, hours_label, sample_days
  windows            one entry per window, the full panel shape: headline,
                     advice, verdict, state, and the reference / observed /
                     projection series the burn-up charts are drawn from
  alerts             what panel.alert_for says currently deserves a notification

A reader decides nothing about pace. Everything here is computed by panel.py,
so a notch panel, the menu bar and a status line all agree because they are
all reading the same sentence.
"""
import datetime
import json
import os
import tempfile

from . import config
from . import panel

PATH = os.path.join(config.DB_DIR, "status.json")
SCHEMA = 1

# The sampler probes every five minutes and backs off to thirty after an HTTP
# 429. Forty-five minutes covers one full back-off plus a missed tick; past
# that, the honest display is nothing.
FRESH_FOR_SECONDS = 45 * 60


def build(usage, limits, priors=None, now=None):
    """The file's contents. Never raises on bad input; ok=False says why."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    full = panel.build(usage or {}, limits or {}, keys=panel.MENUBAR_KEYS, priors=priors)
    wins = full.get("windows") or []
    alerts = [a for a in (panel.alert_for(w) for w in wins) if a]
    return {
        "schema": SCHEMA,
        "written_at": now.isoformat(),
        "checked_at": full.get("checked_at"),
        "fresh_for_seconds": FRESH_FOR_SECONDS,
        "stale": bool((limits or {}).get("stale")),
        "source": (limits or {}).get("strategy"),
        "ok": bool(full.get("ok")),
        "error": full.get("error"),
        "hours_per_day": full.get("hours_per_day"),
        "hours_label": full.get("hours_label"),
        "sample_days": full.get("sample_days"),
        "windows": wins,
        "alerts": alerts,
    }


def write(body, path=None):
    """Atomic: a reader never sees half a file. Returns the path written."""
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".status-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(body, f, default=str)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def is_fresh(body, now=None):
    """The rule every reader should apply, written once so they can copy it."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        t = datetime.datetime.fromisoformat(str(body.get("checked_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    limit = float(body.get("fresh_for_seconds") or FRESH_FOR_SECONDS)
    return bool(body.get("ok")) and (now - t).total_seconds() <= limit
