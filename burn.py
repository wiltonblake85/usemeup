#!/usr/bin/env python3
"""
burn.py - project when a rate-limit window will be exhausted.

No network, no credentials. Pure arithmetic over the utilization samples that
store.py has recorded, plus the current reading.

Two bases, and the tool always says which one it used:

  average  Only one observation is available, so assume the whole window has
           been consumed at a constant rate: final = used / elapsed_fraction.
           Honest but blunt; a quiet Monday followed by a heavy Friday looks
           the same as a steady week.

  recent   Two or more observations spanning enough time to measure a slope.
           Projects forward from the recent rate instead of the lifetime
           average, which is what you want when your pace just changed.

A projection is a straight-line extrapolation of past behaviour, not a
prediction. It is labelled as such in the UI.
"""
import datetime

MIN_SPAN_HOURS = 1.0     # below this a slope is mostly noise
LOOKBACK_HOURS = 36.0    # window of history used for the recent slope


def _parse(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=datetime.timezone.utc) if t.tzinfo is None else t


def project(window, samples, now=None):
    """Return a projection dict for one rate-limit window, or None.

    window: dict with used_pct, window_seconds, resets_in_seconds
    samples: [{ts, used_pct, resets_at}] oldest first, same window key
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    used = window.get("used_pct")
    win_s = window.get("window_seconds")
    left_s = window.get("resets_in_seconds")
    if used is None or not win_s or left_s is None:
        return None

    elapsed_s = max(1.0, win_s - left_s)
    hours_left = left_s / 3600.0
    reset_at = now + datetime.timedelta(seconds=left_s)

    # Only samples from the CURRENT window are usable. A sample taken before the
    # window opened describes a window that has since reset to zero.
    window_start = now - datetime.timedelta(seconds=elapsed_s)
    usable = []
    for s in samples or []:
        t = _parse(s.get("ts"))
        if not t or t < window_start or t > now:
            continue
        # A reset boundary crossed mid-history shows up as utilization falling.
        usable.append((t, float(s.get("used_pct") or 0)))
    usable.sort()
    # Drop everything before the last decrease: that is a prior window's tail.
    for i in range(len(usable) - 1, 0, -1):
        if usable[i][1] < usable[i - 1][1]:
            usable = usable[i:]
            break

    basis, rate_per_hour = "average", used / (elapsed_s / 3600.0)
    if len(usable) >= 2:
        span_h = (usable[-1][0] - usable[0][0]).total_seconds() / 3600.0
        recent = [p for p in usable if (now - p[0]).total_seconds() <= LOOKBACK_HOURS * 3600]
        if len(recent) >= 2:
            span_h = (recent[-1][0] - recent[0][0]).total_seconds() / 3600.0
            if span_h >= MIN_SPAN_HOURS:
                delta = recent[-1][1] - recent[0][1]
                rate_per_hour = delta / span_h
                basis = "recent"

    projected_end = used + rate_per_hour * hours_left
    exhausted_at = None
    if rate_per_hour > 0.01 and used < 100:
        hours_to_cap = (100.0 - used) / rate_per_hour
        if hours_to_cap <= hours_left:
            exhausted_at = (now + datetime.timedelta(hours=hours_to_cap)).isoformat()

    # Series for the chart. The window opened at 0% by definition, so anchoring
    # there gives an honest line even before any samples have accumulated.
    window_start = now - datetime.timedelta(seconds=elapsed_s)
    series = [{"t": window_start.isoformat(), "pct": 0.0}]
    for t, pct in usable:
        series.append({"t": t.isoformat(), "pct": round(pct, 2)})
    series.append({"t": now.isoformat(), "pct": round(float(used), 2)})
    # Collapse duplicate timestamps, keeping the later reading.
    dedup = {}
    for pt in series:
        dedup[pt["t"][:19]] = pt
    series = sorted(dedup.values(), key=lambda x: x["t"])

    return {
        "basis": basis,
        "samples_used": len(usable),
        "rate_pct_per_hour": round(rate_per_hour, 3),
        "projected_end_pct": round(max(0.0, projected_end), 1),
        "will_exhaust": exhausted_at is not None,
        "exhausted_at": exhausted_at,
        "window_start": window_start.isoformat(),
        "resets_at": reset_at.isoformat(),
        "now": now.isoformat(),
        "used_pct": round(float(used), 2),
        "elapsed_fraction": round(elapsed_s / win_s, 4),
        "series": series,
        "headroom_pct": round(max(0.0, 100.0 - projected_end), 1),
    }


def attach(windows, sample_reader):
    """Attach a projection to each window that has one. sample_reader(key) -> samples."""
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=LOOKBACK_HOURS * 2)).isoformat()
    for w in windows or []:
        try:
            w["projection"] = project(w, sample_reader(w.get("key"), since))
        except Exception as e:
            w["projection"] = {"error": "%s: %s" % (type(e).__name__, e)}
    return windows
