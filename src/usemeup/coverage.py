#!/usr/bin/env python3
"""
coverage.py - what the account's rate-limit windows did on each calendar day.

No network, no credentials. Pure arithmetic over the utilization samples that
store.py has recorded.

Why this exists. The spend-by-day chart is built from transcripts on this
machine, and cloud sessions write nothing here. The rate-limit windows are
account-wide, so a day on which a window climbed while the local index holds
nothing is a day of work that happened somewhere this tool cannot see. This
module puts that movement beside each day so the chart can say so on its face
instead of implying the local bars are the whole picture.

For each calendar day (cut in the user's zone, the same cut the transcript
charts use) and each weekly window it reports:

  rise_pct        how many points the window's cumulative figure rose that day
  measured_pct    the part of the rise between samples close enough together
                  to place it (daily.MEASURED_MAX_S); the rest accrued across
                  a sampling gap and is spread across the gap evenly by time
  sampled_hours   hours of the day covered by close-together samples

A window reset inside a pair of samples starts the new window from zero, so the
rise is the new reading, not a negative number. The same attribution rules as
daily.py, on a different day boundary.
"""
import datetime

from . import daily

MEASURED_MAX_S = daily.MEASURED_MAX_S


def _parse(ts):
    return daily._parse(ts)


def _day_start(t, tz):
    local = t.astimezone(tz)
    return datetime.datetime(local.year, local.month, local.day, tzinfo=tz)


def _spread(delta, t0, t1, tz, out, field):
    """Spread a rise over [t0, t1] across the local calendar days it overlaps."""
    cur = _day_start(t0, tz)
    span = (t1 - t0).total_seconds()
    while cur < t1:
        nxt = cur + datetime.timedelta(days=1)
        a, b = max(t0, cur), min(t1, nxt)
        part = (b - a).total_seconds()
        if part > 0:
            key = cur.date().isoformat()
            d = out.setdefault(key, {"rise_pct": 0.0, "measured_pct": 0.0, "sampled_s": 0.0})
            d["rise_pct"] += delta * part / span if span > 0 else delta
            if field == "measured":
                d["measured_pct"] += delta * part / span if span > 0 else delta
                d["sampled_s"] += part
        cur = nxt


def _sampled_only(t0, t1, tz, out):
    """Credit sampled time to the days it covers when nothing rose."""
    cur = _day_start(t0, tz)
    while cur < t1:
        nxt = cur + datetime.timedelta(days=1)
        a, b = max(t0, cur), min(t1, nxt)
        part = (b - a).total_seconds()
        if part > 0:
            d = out.setdefault(cur.date().isoformat(),
                               {"rise_pct": 0.0, "measured_pct": 0.0, "sampled_s": 0.0})
            d["sampled_s"] += part
        cur = nxt


def rise_by_day(samples, tz):
    """{day: {rise_pct, measured_pct, sampled_hours}} for one window's samples."""
    readings = []
    for s in samples or []:
        t, r = _parse(s.get("ts")), _parse(s.get("resets_at"))
        if not t:
            continue
        readings.append((t, float(s.get("used_pct") or 0), daily._minute(r) if r else None))
    readings.sort(key=lambda x: x[0])
    out = {}
    prev = None
    for t, p, reset_key in readings:
        if prev is None:
            prev = (t, p, reset_key)
            continue
        t0, p0, k0 = prev
        if t <= t0:
            continue
        gap = (t - t0).total_seconds()
        direct = gap <= MEASURED_MAX_S
        # A reset between the two samples: the new window opened at 0, so the
        # whole new reading accrued since then. The figure never falls otherwise.
        new_window = (reset_key is not None and k0 is not None and reset_key != k0) or p < p0
        delta = p if new_window else p - p0
        if delta > 0:
            _spread(delta, t0, t, tz, out, "measured" if direct else "estimated")
        elif direct:
            _sampled_only(t0, t, tz, out)
        prev = (t, p, reset_key)
    return {day: {"rise_pct": round(v["rise_pct"], 2),
                  "measured_pct": round(v["measured_pct"], 2),
                  "sampled_hours": round(v["sampled_s"] / 3600.0, 1)}
            for day, v in out.items()}


def scope_of(name):
    """The model a window is scoped to, from its display name, or None.

    rate_limits names a scoped window "<kind>, <Model> only". Anything without
    that suffix is account-wide.
    """
    n = str(name or "")
    if n.endswith(" only") and ", " in n:
        return n.rsplit(", ", 1)[1][:-len(" only")].strip() or None
    return None


def build(samples_by_key, names, tz):
    """{key: {name, scope, days: {day: {...}}}} for every weekly window sampled."""
    out = {}
    for key, samples in (samples_by_key or {}).items():
        if not str(key).startswith("weekly"):
            continue
        name = names.get(key) or key
        out[key] = {"name": name, "scope": scope_of(name), "days": rise_by_day(samples, tz)}
    return out
