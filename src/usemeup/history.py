#!/usr/bin/env python3
"""
history.py - what a typical rate-limit window looks like on this account.

No network, no credentials. Arithmetic over the utilization samples store.py
has already recorded.

Why this exists. pace_basis projects where a window lands by measuring that
window's own slope, which works in the middle of a window and fails at both
ends. A few hours after a reset there is almost no working time on the clock,
so the measured slope is either noise (21% spent across 0.2% of the week's
working hours reads as 8,652% per window) or, once the guard rejects that as
too thin, zero. Zero is the worst answer available: it tells someone who has
been working all morning that they will finish the week exactly where they
stand, which is how a window at 21% on day one claimed it would land at 21%.

The account has history. Earlier windows rose at some rate and ended somewhere,
and that is a far better guess for a nearly empty window than either the noise
or the zero. This module measures it, and pace_basis leans on it until the
window has enough of its own evidence to stand up.

Two things to know about the measurement:

Reset jitter. `resets_at` carries sub-second jitter on every reading and the
jitter crosses minute boundaries, so the same week appears as both
"...T23:59" and "...T00:00". Grouping on the raw value turned two real weekly
windows into 2,217 of them and double-counted the consumption inside them.
Windows are bucketed to WINDOW_BUCKET_S instead.

Censoring. A window that reaches 100% stops rising, so the demand that would
have gone past the cap is invisible. Every figure here is therefore a floor on
real consumption, never an overstatement. For a projection whose job is to warn
about running out, erring low is the safe direction.
"""
import datetime

# Reset timestamps wobble by seconds on every reading. Ten minutes is far wider
# than the observed jitter and far narrower than any real window.
WINDOW_BUCKET_S = 600

# Below this there is not enough observation to claim a rate at all.
MIN_DAYS_OBSERVED = 1.5


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def window_id(resets_at):
    """A stable id for the window a sample belongs to, jitter removed."""
    t = _parse(resets_at)
    if t is None:
        return None
    return int(round(t.timestamp() / WINDOW_BUCKET_S))


# Two consecutive readings are inside the same window when their reset
# timestamps agree to well within a window length. Sub-second jitter passes;
# the nearest real boundary is the 5-hour window, 18,000s away.
SAME_WINDOW_TOLERANCE_S = 120


def same_window(a, b):
    """Are these two reset timestamps the same window?

    Deliberately a tolerance and not window_id(). Bucketing is right for sorting
    many samples into groups, but for comparing two readings it can split a pair
    that straddles a bucket edge, which a tolerance never does. Two jobs, two
    primitives, one module so they cannot drift apart.
    """
    ta, tb = _parse(a), _parse(b)
    if ta is None or tb is None:
        return False
    return abs((ta - tb).total_seconds()) < SAME_WINDOW_TOLERANCE_S


def windows(samples):
    """Group one key's samples into real windows.

    Returns a list of dicts ordered by start, each with:
      id, t0, t1            first and last sample seen inside the window
      peak                  the highest utilisation reached
      rise                  points gained, summing only increases
      sampled_seconds       wall time actually covered by samples
    """
    by_id = {}
    for s in samples:
        wid = window_id(s.get("resets_at"))
        t = _parse(s.get("ts"))
        pct = s.get("used_pct")
        if wid is None or t is None or pct is None:
            continue
        pct = float(pct)
        w = by_id.get(wid)
        if w is None:
            by_id[wid] = {"id": wid, "t0": t, "t1": t, "peak": pct,
                          "rise": 0.0, "_prev": pct}
            continue
        if pct > w["_prev"]:
            w["rise"] += pct - w["_prev"]
        w["_prev"] = pct
        w["t1"] = t
        w["peak"] = max(w["peak"], pct)

    out = []
    for w in by_id.values():
        w.pop("_prev", None)
        w["sampled_seconds"] = (w["t1"] - w["t0"]).total_seconds()
        out.append(w)
    out.sort(key=lambda w: w["t0"])
    return out


def typical_full_window(samples, window_seconds, exclude_id=None):
    """How much a full window of this length usually consumes.

    The answer is in the same units pace_basis calls slope: points per complete
    window, where duty runs 0 to 1 across the window. Measured as the observed
    consumption rate per day, scaled to the window's length, which needs far
    less history than waiting for whole windows to complete and is not censored
    by the ones that hit the cap mid-week.

    `exclude_id` drops the window being projected, so a window never helps
    predict itself.

    Returns None when there is too little history to say anything. None means
    "no opinion" and the caller must say so rather than substitute a number.
    """
    if not samples or not window_seconds:
        return None
    ws = [w for w in windows(samples) if w["id"] != exclude_id]
    if not ws:
        return None

    rise = sum(w["rise"] for w in ws)
    # Wall time covered, summed per window so gaps between windows and the
    # excluded current one do not inflate the denominator.
    seconds = sum(w["sampled_seconds"] for w in ws)
    days = seconds / 86400.0
    if days < MIN_DAYS_OBSERVED or rise <= 0:
        return None

    per_day = rise / days
    return {
        "expected_end": per_day * (window_seconds / 86400.0),
        "per_day": per_day,
        "days_observed": days,
        "windows_seen": len(ws),
        "capped_windows": sum(1 for w in ws if w["peak"] >= 100),
    }


def priors(samples_by_key, window_seconds_by_key, current_ids=None):
    """{key: typical_full_window(...)} for every key with usable history."""
    current_ids = current_ids or {}
    out = {}
    for key, samples in (samples_by_key or {}).items():
        ws = window_seconds_by_key.get(key)
        if not ws:
            continue
        t = typical_full_window(samples, ws, exclude_id=current_ids.get(key))
        if t:
            out[key] = t
    return out
