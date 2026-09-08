#!/usr/bin/env python3
"""
daily.py - the weekly rate-limit window as seven daily buckets.

No network, no credentials. Pure arithmetic over the utilization samples that
store.py has recorded, plus the current reading.

The API reports one cumulative figure for the week. There is no daily allowance
on Anthropic's side; this module defines one as a convention so a week can be
read the way people actually plan it:

  allotment   one seventh of the window (14.29%) per day
  day         a 24 hour bucket ending at the same clock time the week resets,
              so the seven buckets sum exactly to the weekly figure and the
              last one ends at reset
  consumed    how much the cumulative figure rose during that bucket
  bank        allotment not spent on earlier days, carried forward, or the
              deficit when earlier days overspent

How a rise is attributed. Two samples taken close together (within
MEASURED_MAX_S) put their rise squarely in one moment, so that share is
"measured". A rise across a longer gap (the page was closed, the server was
down) is real usage but its timing is not known. It is spread evenly across the
gap, reported separately as "estimated", and the gap itself is returned so the
chart can bracket those days and say how much accrued with unknown timing.

Why evenly and not by some proxy. An earlier version placed gap usage by the
token volume in the local transcript index. Cloud sessions leave no transcripts
on this machine, so that proxy is blind to exactly the work it was meant to
locate, and it manufactured a confident-looking daily split that was wrong.
An even spread claims nothing about timing; the bracket makes that explicit.

The cumulative figure is reported in whole percent, so a day below 1% of the
week reads as 0.
"""
import datetime

DAY_S = 86400
WEEK_DAYS = 7
ALLOTMENT_PCT = 100.0 / WEEK_DAYS
MEASURED_MAX_S = 20 * 60   # samples this close together count as a direct measurement
WEEKS_KEPT = 5             # current week plus up to four prior ones


def _parse(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=datetime.timezone.utc) if t.tzinfo is None else t


def _minute(t):
    """Group key for a reset time: reset timestamps drift by milliseconds between probes."""
    return int(round(t.timestamp() / 60.0))


def _overlap(a0, a1, b0, b1):
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


def _split(delta, t0, t1, buckets):
    """Spread a rise over [t0, t1] across the buckets it overlaps, by time."""
    hits = [(i, _overlap(t0, t1, b0, b1)) for i, (b0, b1) in enumerate(buckets)]
    hits = [(i, s) for i, s in hits if s > 0]
    if not hits:
        return []
    if len(hits) == 1:
        return [(hits[0][0], delta)]
    span = float(sum(s for _, s in hits))
    return [(i, delta * s / span) for i, s in hits]


def _week(reset, readings, now, current):
    """One window instance as seven buckets. readings: sorted [(t, pct)]."""
    start = reset - datetime.timedelta(days=WEEK_DAYS)
    buckets = [(start + datetime.timedelta(days=i), start + datetime.timedelta(days=i + 1))
               for i in range(WEEK_DAYS)]
    measured = [0.0] * WEEK_DAYS
    estimated = [0.0] * WEEK_DAYS
    gaps = []
    covered_s = 0.0

    prev_t, prev_p = start, 0.0          # the window opened at 0% by definition
    for t, p in readings:
        if t <= prev_t:
            continue
        delta = max(0.0, p - prev_p)     # the figure never falls inside one window
        gap_s = (t - prev_t).total_seconds()
        direct = gap_s <= MEASURED_MAX_S
        if direct:
            covered_s += gap_s
        else:
            gaps.append({"start": prev_t.isoformat(), "end": t.isoformat(),
                         "seconds": int(gap_s), "pct": round(delta, 2)})
        if delta > 0:
            for i, share in _split(delta, prev_t, t, buckets):
                (measured if direct else estimated)[i] += share
        prev_t, prev_p = t, p

    end_seen = min(now, reset)
    elapsed_s = max(1.0, (end_seen - start).total_seconds())
    days = []
    bank = 0.0                            # allotment carried in from completed days
    bank_before_today = None
    for i, (b0, b1) in enumerate(buckets):
        consumed = measured[i] + estimated[i]
        if b1 <= now:
            status = "past"
        elif b0 <= now:
            status = "today"
        else:
            status = "future"
        d = {
            "start": b0.isoformat(), "end": b1.isoformat(), "status": status,
            "consumed_pct": round(consumed, 2),
            "measured_pct": round(measured[i], 2),
            "estimated_pct": round(estimated[i], 2),
            "allotment_pct": round(ALLOTMENT_PCT, 2),
            "over_pct": round(consumed - ALLOTMENT_PCT, 2) if status != "future" else None,
        }
        if status == "past":
            bank += ALLOTMENT_PCT - consumed
            d["bank_after_pct"] = round(bank, 2)
        elif status == "today":
            bank_before_today = bank
            d["available_pct"] = round(ALLOTMENT_PCT + bank, 2)
            d["remaining_pct"] = round(ALLOTMENT_PCT + bank - consumed, 2)
        days.append(d)

    total = sum(measured) + sum(estimated)
    today = next((d for d in days if d["status"] == "today"), None)
    return {
        "current": current,
        "window_start": start.isoformat(),
        "resets_at": reset.isoformat(),
        "days": days,
        "gaps": [g for g in gaps if g["pct"] > 0],   # only gaps in which usage accrued
        "total_pct": round(total, 2),
        "measured_share": round(sum(measured) / total, 3) if total > 0 else None,
        "coverage": round(min(1.0, covered_s / elapsed_s), 3),
        "readings": len(readings),
        "today": today,
        "bank_before_today_pct": round(bank_before_today, 2) if bank_before_today is not None else None,
    }


def build(window, samples, now=None):
    """Daily buckets for one weekly window, or None when it is not a 7-day window.

    window:  dict with key, used_pct, window_seconds, resets_at
    samples: [{ts, used_pct, resets_at}] any order, same window key
    """
    if not window or window.get("window_seconds") != WEEK_DAYS * DAY_S:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cur_reset = _parse(window.get("resets_at"))
    if not cur_reset or window.get("used_pct") is None:
        return None

    groups = {}
    for s in samples or []:
        t, r = _parse(s.get("ts")), _parse(s.get("resets_at"))
        if not t or not r or t > now:
            continue
        groups.setdefault(_minute(r), (r, []))[1].append((t, float(s.get("used_pct") or 0)))
    cur_key = _minute(cur_reset)
    groups.setdefault(cur_key, (cur_reset, []))[1].append((now, float(window["used_pct"])))

    weeks = []
    for key in sorted(groups, reverse=True):
        reset, readings = groups[key]
        if key > cur_key:
            continue                      # a future reset makes no sense; skip it
        readings.sort()
        weeks.append(_week(reset, readings, now, current=(key == cur_key)))
        if len(weeks) >= WEEKS_KEPT:
            break

    first = min((_parse(s.get("ts")) for s in samples or [] if _parse(s.get("ts"))), default=None)
    return {
        "allotment_pct": round(ALLOTMENT_PCT, 2),
        "measured_max_seconds": MEASURED_MAX_S,
        "sampling_since": first.isoformat() if first else None,
        "weeks": weeks,
    }


def attach(windows, sample_reader, now=None):
    """Attach daily buckets to each weekly window. sample_reader(key, since) -> samples."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=WEEK_DAYS * WEEKS_KEPT)).isoformat()
    for w in windows or []:
        if w.get("window_seconds") != WEEK_DAYS * DAY_S:
            continue
        try:
            w["daily"] = build(w, sample_reader(w.get("key"), since), now)
        except Exception as e:
            w["daily"] = {"error": "%s: %s" % (type(e).__name__, e)}
    return windows
